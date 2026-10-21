# ALIGN actual parser benchmark

Generated at: `2026-10-21T09:29:01.953805+00:00`
Profile: `native_ooxml_v1` · profile SHA-256 `8bbb8e3601515fe7d370acedc3908887e58b6be7065f81b468154440448c3978`
Execution: `actual-isolated-parser-child; mock=false`; synthetic fixtures only; no mock parser or provider call.

## Environment

- Platform: `Linux-7.0.14-linuxkit-aarch64-with-glibc2.41` / `aarch64`
- CPU count: `8`
- Host/VM memory: `12526637056` bytes; container limit: `9663676416`
- Packages: `{"docling": "not-installed", "docling-core": "2.102.0", "docling-slim": "2.140.0", "openpyxl": "3.1.5", "pillow": "12.3.0", "pypdf": "6.21.1", "python-docx": "1.2.0", "torch": "2.14.1+cpu", "torchvision": "0.29.1+cpu"}`
- Parser resource profile: `{"backend": "python-docx+ooxml", "formats": ["docx", "xlsx"], "hard_timeout_seconds": 150, "heavy": false, "implementation_sha256": "0d6d9806e1e7bfe4c7ef6aae370378f0d18e88b18072d694c7820f53fc8ecc2d", "initial_page_batch_size": 1, "memory_limit_bytes": 2147483648, "model_artifacts": [], "ocr": null, "options": {}, "options_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", "package_lock": {"docling-slim": "2.140.0", "openpyxl": "3.1.5", "python-docx": "1.2.0", "uv_lock_sha256": "eee5c7af8580501b4bcb0fd8b4e47bb1c31fc3c7043b10e55b81db7ab3f59097"}, "profile_key": "native_ooxml_v1", "schema_version": 1, "soft_timeout_seconds": 120, "version_no": 1}`
- Cold means the first isolated child run in this benchmark invocation; warm means a second isolated child run after OS file/model artifacts may be cached. Each child reloads the parser and model.
- Child RSS is `/proc/<pid>/status:VmHWM`; cgroup peak is a 50 ms sampled whole-container `memory.current`, including the benchmark parent and any other resident container processes, not a per-child cgroup measurement.

## Results

| Fixture | Format | SHA-256 | Preflight (ms) | Cold wall (ms) | Warm wall (ms) | Peak child RSS (bytes) | Peak container cgroup sample (bytes) | Anchor checks | Tables | Failures |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---|
| `complex-submission.docx` | docx | `8fe525f17cbf0ec80c185a4a01ac2631b7eea2a6d26da0a51378ccf2501ab1a9` | 8 | 257 | 255 | 38641664 | 141123584 | {'verified': 33, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 33, 'invalid': 0, 'unanchored_or_unsupported': 0} | 10 / 10 | [] / [] |
| `complex-submission.xlsx` | xlsx | `0ce5725c0339e2a3b548d9cc5ba419b13488557779f810a2a90202931802467e` | 13 | 366 | 316 | 47792128 | 145465344 | {'verified': 22, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 22, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |

## Stage and page timing

Preflight is timed separately. Child wall time includes interpreter startup, parser work, result serialization, and process cleanup; stage timings below are measured inside the child and are not added to wall time.
Docling exposes render/layout/OCR/table work as one conversion call in this pinned integration, so `docling_pipeline_conversion` is reported as a combined stage rather than splitting unobservable internal timings.

| Fixture | Run | Page | Child wall (ms) | Measured stage timings (ms) |
|---|---|---:|---:|---|
| `complex-submission.docx` | cold | file | 257 | `{"docx_ooxml_extraction_inventory": 97.306}` |
| `complex-submission.docx` | warm | file | 255 | `{"docx_ooxml_extraction_inventory": 85.411}` |
| `complex-submission.xlsx` | cold | file | 366 | `{"xlsx_ooxml_extraction_inventory": 187.52}` |
| `complex-submission.xlsx` | warm | file | 316 | `{"xlsx_ooxml_extraction_inventory": 153.932}` |

## Support boundaries

- OCR output is an extracted technical artifact, not verified handwriting recognition.
- Spreadsheet formulas are never executed; cached values remain unverified.
- Charts, drawings and embedded media are inventory items and remain unsupported unless the parser emits verified content.
- A successful sample is not evidence that every allowed 100-page or maximum-size bundle finishes within 600 active seconds.
- Docling 2.140.0 reports render/layout/OCR/table work as a combined conversion call; stage timings do not claim unexposed internal substage measurements.
