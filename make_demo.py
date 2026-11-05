#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Генерирует демо-таблицу (xlsx) для проверки трекера без доступа к Google.

    python make_demo.py            -> demo_step1.xlsx
    python make_demo.py --step 2   -> demo_step2.xlsx (с изменениями)

Шаг 2 имитирует то, что должно произойти в жизни:
    * машине в Казани назначили номер Т/С   (прибыла)
    * строка Самары из зелёной стала жёлтой (выехала)
    * в новой строке появилась красная ячейка (форс-мажор)
"""

import argparse
import zipfile
from xml.sax.saxutils import escape

GREEN = "92D050"
YELLOW = "FFEB9C"
RED = "FF5050"

HEADERS = [
    "ГОРОД", "№ МЛ", "Объем", "Навигация", "ЭТО",
    "Шины", "Диски", "Аксы", "ВЫВОЗ", "Сортировка", "Номер Т/С", "Комментарии",
]

STEP1 = [
    # город, МЛ, объём, навигация, ответственный, номер Т/С, цвет строки, коммент
    ("МО, зона 1", "00051031", "91,7", "A8(9)", "Хлопок", "А123ВС77", None, ""),
    ("Казань", "00051130", "16,4", "B4(7)", "Минеев", "", None, "будет в 8-15"),
    ("Самара", "00051217", "34,5", "A11(1)", "Минеев", "", GREEN, "Шины, Диски, Акс"),
    ("Тольятти", "00051197", "22,8", "B23(6)", "Ильин", "", None, ""),
]

STEP2 = [
    ("МО, зона 1", "00051031", "91,7", "A8(9)", "Хлопок", "А123ВС77", None, ""),
    ("Казань", "00051130", "16,4", "B4(7)", "Минеев", "В777АК16", None, "будет в 8-15"),
    ("Самара", "00051217", "34,5", "A11(1)", "Минеев", "", YELLOW, "Шины, Диски, Акс"),
    ("Тольятти", "00051197", "22,8", "B23(6)", "Ильин", "", None, ""),
    ("Москва, день в день", "00051135", "108,4", "A13(13)", "Минеев", "", RED, "Срок бита"),
]


def col_letter(idx: int) -> str:
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def build(rows, path: str) -> None:
    palette = [c for c in (GREEN, YELLOW, RED) if any(r[6] == c for r in rows)]
    palette += [c for c in (RED,) if c not in palette]

    # индекс стиля = позиция в палитре + 1 (нулевой стиль — обычная ячейка)
    fill_index = {c: i + 1 for i, c in enumerate(palette)}
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(text: str) -> int:
        if text not in index:
            index[text] = len(strings)
            strings.append(text)
        return index[text]

    sheet_rows = []
    header_cells = "".join(
        f'<c r="{col_letter(i + 1)}2" t="s" s="0"><v>{sid(h)}</v></c>'
        for i, h in enumerate(HEADERS)
    )
    sheet_rows.append(f'<row r="2">{header_cells}</row>')

    for n, (city, ml, volume, nav, who, plate, color, comment) in enumerate(rows):
        r = 4 + n
        style = fill_index.get(color, 0) if color else 0
        values = [city, ml, volume, nav, who, "+", "", "", "", who, plate, comment]
        cells = []
        for i, val in enumerate(values):
            if val == "":
                continue
            s = style if (color and i < 5) else 0
            cells.append(
                f'<c r="{col_letter(i + 1)}{r}" t="s" s="{s}"><v>{sid(val)}</v></c>'
            )
        if color == RED:
            cells.append(
                f'<c r="L{r}" t="s" s="{fill_index[RED]}"><v>{sid(comment or "форс")}</v></c>'
            )
        sheet_rows.append(f'<row r="{r}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )

    fills = ['<fill><patternFill patternType="none"/></fill>',
             '<fill><patternFill patternType="gray125"/></fill>']
    for c in palette:
        fills.append(
            '<fill><patternFill patternType="solid">'
            f'<fgColor rgb="FF{c}"/><bgColor indexed="64"/>'
            "</patternFill></fill>"
        )

    xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>']
    for i in range(len(palette)):
        xfs.append(
            f'<xf numFmtId="0" fontId="0" fillId="{i + 2}" borderId="0" xfId="0" applyFill="1"/>'
        )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        f'<fills count="{len(fills)}">{"".join(fills)}</fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        f'<cellXfs count="{len(xfs)}">{"".join(xfs)}</cellXfs>'
        "</styleSheet>"
    )

    sst = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
        + "</sst>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Лист1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    print(f"Создан {path}: строк {len(rows)}, цветов {len(palette)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=1, choices=[1, 2])
    args = ap.parse_args()
    rows = STEP1 if args.step == 1 else STEP2
    build(rows, f"demo_step{args.step}.xlsx")
