"""Standalone UNO bridge: open DOCX, refresh fields/TOC, export PDF.

Run with a Python that can ``import uno`` (usually the system Python that ships
with LibreOffice). Kept separate from the package so the CLI never needs uno.
"""

from __future__ import annotations

import sys
import time

import uno
from com.sun.star.beans import PropertyValue


def _prop(name: str, value: object) -> PropertyValue:
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def main() -> int:
    source, target, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    context = None
    for _ in range(120):
        try:
            context = resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
            )
            break
        except Exception:  # noqa: BLE001 - UNO raises dynamic connection errors
            time.sleep(0.5)
    if context is None:
        return 2

    desktop = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", context
    )
    document = desktop.loadComponentFromURL(
        uno.systemPathToFileUrl(source),
        "_blank",
        0,
        (_prop("Hidden", True),),
    )
    try:
        document.refresh()
        for index in document.getDocumentIndexes():
            index.update()
        document.refresh()
        document.storeToURL(
            uno.systemPathToFileUrl(target),
            (_prop("FilterName", "writer_pdf_Export"),),
        )
    finally:
        document.close(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
