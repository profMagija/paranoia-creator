from dataclasses import dataclass
import json
import os
import random
from typing import List, Set, Tuple

import click
import fpdf
import yaml

from base64 import b64decode, b64encode

CONFIG_FILE_NAME = "paranoia.yml"
ORGA_FILE_NAME = ".organization"


@dataclass
class DataField:
    name: str
    is_player: bool = False
    can_repeat: bool = False
    can_skip: bool = False


root_dir_argument = lambda: click.argument(
    "root_dir",
    type=click.types.Path(
        exists=True,
        file_okay=False,
        dir_okay=True,
        writable=True,
        readable=True,
        resolve_path=True,
    ),
    default=".",
)


@click.group
def paranoia():
    pass


def _error_out(desc: str):
    click.secho(
        f"Error: {desc}",
        fg="red",
    )
    click.secho("Aborting!", fg="red")
    raise click.exceptions.Exit(2)


############################################################
#
# ORGANIZATION PART
#
############################################################


def _create_orga(root_dir, paranoia_file):
    fields = [DataField(**conf) for conf in paranoia_file["fields"]]

    # check all names unique
    if len({field.name for field in fields}) != len(fields):
        duplicates = ", ".join(
            {
                field.name
                for field in fields
                if any(f2.name == field.name for f2 in fields if f2 is not field)
            }
        )
        _error_out("duplicate field names: " + duplicates)

    if len([0 for field in fields if field.is_player]) != 1:
        _error_out("exactly one field must have `is_player' set")

    player_field = [field.name for field in fields if field.is_player][0]

    field_data = {}
    for field in fields:
        data_path = os.path.join(root_dir, field.name + ".txt")

        if not os.path.exists(data_path):
            raise click.FileError(data_path, f"data for {field.name} not found")

        with click.open_file(data_path, encoding="utf-8") as f:
            field_data[field.name] = [line.strip() for line in f if line.strip()]

    player_count = len(field_data[player_field])

    for field in fields:
        count = len(field_data[field.name])
        if field.is_player:
            if field.can_repeat or field.can_skip:
                _error_out('name field must not be marked "can_repeat" or "can_skip"')
        if not field.can_skip and count > player_count:
            _error_out(
                f'field {field.name} has too many entries, and is not marked "can_skip". Got {count}, need {player_count}'
            )
        if not field.can_repeat and count < player_count:
            _error_out(
                f'field {field.name} has too little entries, and is not marked "can_repeat". Got {count}, need {player_count}'
            )

    # create the orga lists
    id_list = list(range(player_count))
    random.shuffle(id_list)

    player_list = list(field_data[player_field])
    random.shuffle(player_list)

    target_list = [*player_list[1:], player_list[0]]

    other_lists = []
    for field in fields:
        if field.is_player:
            continue

        data = list(field_data[field.name])
        if field.can_repeat and field.can_skip:
            data = random.sample(data, k=player_count)
        elif len(data) < player_count:
            data.extend(random.choices(data, k=player_count - len(data)))
        elif len(data) > player_count:
            data = random.sample(data, k=player_count)
        random.shuffle(data)
        other_lists.append(data)

    orga_table = [id_list, player_list, target_list, *other_lists]

    # transpose
    orga_table = [[col[i] for col in orga_table] for i in range(player_count)]

    orga_table.sort()

    assert len(orga_table) == player_count
    assert all(len(col) == 2 + len(fields) for col in orga_table)

    return orga_table


def _print_orga(orga_table):
    orga_table = [[str(x) for x in y] for y in orga_table]
    measure = click.formatting.measure_table(orga_table)
    for row in orga_table:
        for i, col in enumerate(row):
            col += " " * (measure[i] - len(col) + 4)
            print(col, end="")
        print()


def _do_organize(root_dir: str, force: bool, print_table: bool):
    config_file_path = os.path.join(root_dir, CONFIG_FILE_NAME)
    orga_file_path = os.path.join(root_dir, ORGA_FILE_NAME)

    if not os.path.exists(config_file_path):
        raise click.FileError(config_file_path, "main configuration file not found")

    if os.path.exists(orga_file_path) and not force:
        _error_out("game already organized, and --force not specified")

    with click.open_file(config_file_path, encoding="utf-8") as config_file:
        paranoia_file = yaml.load(config_file, yaml.SafeLoader)

    orga_table = _create_orga(root_dir, paranoia_file)

    if print_table:
        confirmation = click.confirm("Are you sure you want to print the table?")
        if confirmation:
            _print_orga(orga_table)

    with click.open_file(orga_file_path, "w") as orga_file:
        orga_ser = json.dumps(orga_table)
        orga_ser = b64encode(orga_ser.encode()).decode()
        orga_file.write(orga_ser)


@root_dir_argument()
@click.option("--force", "-f", is_flag=True)
@click.option("--print-table", is_flag=True)
@paranoia.command
def organize(root_dir: str, force: bool, print_table: bool):
    _do_organize(root_dir, force, print_table)


############################################################
#
# PRESENTATION PART
#
############################################################


@dataclass
class Config:
    cover_font_name: str = "Arial"
    cover_font_style: str = "B"
    cover_font_size: int = 20
    cover_line_spacing: str = 1.1

    field_font_name: str = "Arial"
    field_font_style: str = ""
    field_font_size: int = 10
    field_line_spacing: str = 1.1

    value_font_name: str = "Arial"
    value_font_style: str = "B"
    value_font_size: int = 12
    value_line_spacing: str = 1.1

    id_font_name: str = "Arial"
    id_font_style: str = "B"
    id_font_size: int = 8
    id_line_spacing: str = 1.1
    id_prefix: str = "Serial Number: "

    print_margin: int = 20
    print_fold_lines: bool = False


PT_TO_MM = 0.3527777778


def _line_height(font_size, line_spacing):
    return int(font_size) * PT_TO_MM * float(line_spacing)


def _create_pdf(
    orga_table: List[Tuple[int, str, str]],
    config: Config,
    fields: List[DataField],
    only: Set[int],
):
    pdf = fpdf.FPDF("P", "mm", "A5")

    pdf.add_page()

    for row in orga_table:
        row_id, person_name, *other = row
        assert len(other) == len(fields)

        if only and row_id not in only:
            continue

        pdf.add_page()

        margin = int(config.print_margin)

        w_mid = pdf.w / 2
        h_mid = pdf.h / 2

        if config.print_fold_lines:
            pdf.line(0, h_mid, pdf.w, h_mid)
            pdf.line(w_mid, 0, w_mid, pdf.h)

        # format lhs

        pdf.set_xy(margin, margin)
        pdf.set_font(
            config.cover_font_name,
            config.cover_font_style,
            int(config.cover_font_size),
        )

        pdf.multi_cell(
            w=w_mid - margin * 2,
            h=_line_height(config.cover_font_size, config.cover_line_spacing),
            txt=person_name,
            align="C",
        )

        pdf.set_y(pdf.y + float(config.cover_font_size) * PT_TO_MM * 0.5)

        pdf.set_xy(margin, h_mid - margin - int(config.id_font_size) * PT_TO_MM)
        pdf.set_font(
            config.id_font_name,
            config.id_font_style,
            int(config.id_font_size),
        )
        pdf.multi_cell(
            w=w_mid - margin * 2,
            h=float(config.value_font_size) * PT_TO_MM * 0.5,
            txt=f"{config.id_prefix}{row_id}",
        )

        # format rhs
        pdf.set_y(margin)
        for field, value in zip(fields, other):
            # field name
            pdf.set_x(margin + w_mid)
            pdf.set_font(
                config.field_font_name,
                config.field_font_style,
                int(config.field_font_size),
            )
            pdf.multi_cell(
                w=w_mid - margin * 2,
                h=_line_height(config.field_font_size, config.field_line_spacing),
                txt=field.name,
            )

            # value
            pdf.set_x(margin + w_mid)
            pdf.set_font(
                config.value_font_name,
                config.value_font_style,
                int(config.value_font_size),
            )
            pdf.multi_cell(
                w=w_mid - margin * 2,
                h=_line_height(config.value_font_size, config.value_line_spacing),
                txt=value,
            )

            # spacing
            pdf.set_y(pdf.y + float(config.value_font_size) * PT_TO_MM * 0.5)

        pdf.set_xy(margin + w_mid, h_mid - margin - int(config.id_font_size) * PT_TO_MM)
        pdf.set_font(
            config.id_font_name,
            config.id_font_style,
            int(config.id_font_size),
        )
        pdf.multi_cell(
            w=w_mid - margin * 2,
            h=float(config.value_font_size) * PT_TO_MM * 0.5,
            txt=f"{config.id_prefix}{row_id}",
        )

    pdf.output("output.pdf", "F")


@root_dir_argument()
@click.option("--only")
@paranoia.command()
def print(root_dir: str, only: str):
    config_file_path = os.path.join(root_dir, CONFIG_FILE_NAME)
    orga_file_path = os.path.join(root_dir, ORGA_FILE_NAME)

    if not os.path.exists(config_file_path):
        raise click.FileError(config_file_path, "main configuration file not found")

    with click.open_file(config_file_path, encoding="utf-8") as config_file:
        paranoia_file = yaml.load(config_file, yaml.SafeLoader)

    if not os.path.exists(orga_file_path):
        if click.confirm("Game not organized. Organize now?"):
            _do_organize(root_dir, False, False)
        else:
            _error_out("game not organized")

    with click.open_file(orga_file_path) as orga_file:
        orga_table = json.loads(b64decode(orga_file.read().encode()).decode())

    config = Config(**paranoia_file["config"])
    fields = [DataField(**field) for field in paranoia_file["fields"]]

    if only:
        only = set(int(i) for i in only.split(","))

    _create_pdf(orga_table, config, fields, only)


if __name__ == "__main__":
    paranoia()
