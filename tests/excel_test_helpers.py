"""シートの格納順に依存せず、保存された帳票と主表原値を調べる。"""

import posixpath
from xml.etree import ElementTree as ET

NS = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheet_xml(archive, name):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = next(s for s in workbook.findall("m:sheets/m:sheet",NS) if s.get("name")==name)
    rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    target = next(r.get("Target") for r in relations if r.get("Id")==rid)
    path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl",target))
    return ET.fromstring(archive.read(path))


def print_names(archive, name):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    names = [s.get("name") for s in workbook.findall("m:sheets/m:sheet",NS)]
    index = str(names.index(name))
    return {n.get("name"):n.text for n in workbook.findall("m:definedNames/m:definedName",NS)
            if n.get("localSheetId")==index}


def source_cell(book, operation, line, field):
    name,r,c = book.sources[operation,line,field]
    return book.sheet(name).rows[r][c]
