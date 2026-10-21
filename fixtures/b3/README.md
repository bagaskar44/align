# B3 submission parser fixtures

Synthetic-only files for the B3 parser benchmark and browser/API flows. They contain no student work or credentials. The PDFs and images are exact copies of the B1 parser fixtures so the same difficult inputs exercise the submission namespace; `complex-submission.docx` adds nested tables and header/footer parts; `complex-submission.xlsx` includes a hidden sheet, merged title, number format, formula without a recalculated cache, and a chart. Chart interpretation and formula cache verification remain unsupported and must remain in inventory/limitations.

The generated DOCX/XLSX bytes are checked in as test inputs. `scripts/generate-b3-fixtures.py` regenerates them using the locked `python-docx` and `openpyxl` dependencies; OOXML ZIP timestamps mean generated file hashes can change. The handover records hashes for the exact files used in its benchmark.
