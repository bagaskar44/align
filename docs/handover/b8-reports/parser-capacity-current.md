# ALIGN actual parser benchmark

Generated at: `2026-10-22T04:56:39.603139+00:00`
Profile: `docling_native_v1` · profile SHA-256 `22794ebaa384b628d581a2a2620ddfaf89a182afaeb53ca01905569a0771f74c`
Execution: `actual-isolated-parser-child; mock=false`; synthetic fixtures only; no mock parser or provider call.

## Environment

- Platform: `Linux-7.0.14-linuxkit-aarch64-with-glibc2.41` / `aarch64`
- CPU count: `8`
- Host/VM memory: `12526637056` bytes; container limit: `9663676416`
- Packages: `{"docling": "not-installed", "docling-core": "2.102.0", "docling-slim": "2.140.0", "openpyxl": "3.1.5", "pillow": "12.3.0", "pypdf": "6.21.1", "python-docx": "1.2.0", "torch": "2.14.1+cpu", "torchvision": "0.29.1+cpu"}`
- Parser resource profile: `{"backend": "docling", "formats": ["pdf", "jpg", "jpeg", "png"], "hard_timeout_seconds": 150, "heavy": true, "implementation_sha256": "4d59ed4d58dfd27018596f7c6d4b31ff49f8a264d62a950215ff9978dd82534f", "initial_page_batch_size": 1, "memory_limit_bytes": 8589934592, "model_artifacts": [{"files": [{"path": "README.md", "sha256": "175700839bc7808eac6af1d0c23e4f483606ab2276fe01122f4093e61a1a65b6", "size_bytes": 3219}, {"path": "config.json", "sha256": "fdea30805ce2f5666b147fca941dcdd27ad468e27d6ed21902207d3da056a97d", "size_bytes": 3268}, {"path": "docling_heron_400.png", "sha256": "e7f78610372b32a7938e480d2c7fa1c3037ee170bd82282a5bd026232f6e6f9e", "size_bytes": 96925}, {"path": "model.safetensors", "sha256": "00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c", "size_bytes": 171658996}, {"path": "preprocessor_config.json", "sha256": "cd38cd59999e7a95d68e487fbe5132df3d4e5c32a0836add57e6126ba0c4eaf1", "size_bytes": 444}], "repo_id": "docling-project/docling-layout-heron", "revision": "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"}, {"files": [{"path": "model_artifacts/tableformer/accurate/tableformer_accurate.safetensors", "sha256": "2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9", "size_bytes": 212758388}, {"path": "model_artifacts/tableformer/accurate/tm_config.json", "sha256": "984e122ceb8ccf84d84c9d2882f6f2302a44b4f1e577babd6289892c36f3cffd", "size_bytes": 7060}], "repo_id": "docling-project/docling-models", "revision": "fc0f2d45e2218ea24bce5045f58a389aed16dc23"}], "ocr": {"artifacts": [{"path": "/usr/share/tesseract-ocr/5/tessdata/ind.traineddata", "sha256": "69786901da87ab8766c1ea7fbb10b28f2110c14da3f6c8f2735df131fba95d88"}, {"path": "/usr/share/tesseract-ocr/5/tessdata/eng.traineddata", "sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"}], "engine": "tesseract", "languages": ["ind", "eng"], "version": "5.5.0"}, "options": {"layout": "layout_heron", "ocr_mode": "native_text_or_full_page", "remote_services": false, "table_mode": "accurate"}, "options_hash": "ffa111253465f97feeaeb171d21bbcd921055cdf6b1c9a59510c5aab2faf99f6", "package_lock": {"docling-slim": "2.140.0", "openpyxl": "3.1.5", "python-docx": "1.2.0", "uv_lock_sha256": "eee5c7af8580501b4bcb0fd8b4e47bb1c31fc3c7043b10e55b81db7ab3f59097"}, "profile_key": "docling_native_v1", "schema_version": 1, "soft_timeout_seconds": 120, "version_no": 1}`
- Cold means the first isolated child run in this benchmark invocation; warm means a second isolated child run after OS file/model artifacts may be cached. Each child reloads the parser and model.
- Child RSS is `/proc/<pid>/status:VmHWM`; cgroup peak is a 50 ms sampled whole-container `memory.current`, including the benchmark parent and any other resident container processes, not a per-child cgroup measurement.

## Results

| Fixture | Format | SHA-256 | Preflight (ms) | Cold wall (ms) | Warm wall (ms) | Peak child RSS (bytes) | Peak container cgroup sample (bytes) | Anchor checks | Tables | Failures |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---|
| `native-two-pages.pdf` | pdf | `04ba3a7fc49f07b407a75ee2861737fe7d80aa7c70dcaf9f25a178cc28c55d6c` | 122 | 27220 | 24590 | 1170583552 | 1023700992 | {'verified': 4, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 4, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |
| `printed.jpeg` | jpeg | `cb2ee782a9c88c93d46496cd6fa6eb517f0d53b313518b8bb33d24109cbbba1c` | 14 | 12960 | 12625 | 1168715776 | 1126420480 | {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |
| `printed.jpg` | jpg | `cb2ee782a9c88c93d46496cd6fa6eb517f0d53b313518b8bb33d24109cbbba1c` | 5 | 14292 | 13392 | 1170034688 | 1131515904 | {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |
| `printed.png` | png | `f2843abac997a191a13cc50a72614774814bc1bcc8edb69ab107de5201b22405` | 10 | 13591 | 13913 | 1171890176 | 1134333952 | {'verified': 4, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 4, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |
| `rotated.jpeg` | jpeg | `8cca617e70ac55a94863d5b278244e244678c08faff3e4414bcb4ee47b668606` | 14 | 13774 | 15145 | 1176338432 | 1138831360 | {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 6, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |
| `scanned.pdf` | pdf | `8be99fac9bca43912668bd3328f8e12bc6b66377a518a98b87943ed8db37c39d` | 4 | 14790 | 13866 | 1194590208 | 1119924224 | {'verified': 5, 'invalid': 0, 'unanchored_or_unsupported': 0} / {'verified': 5, 'invalid': 0, 'unanchored_or_unsupported': 0} | 0 / 0 | [] / [] |

## Stage and page timing

Preflight is timed separately. Child wall time includes interpreter startup, parser work, result serialization, and process cleanup; stage timings below are measured inside the child and are not added to wall time.
Docling exposes render/layout/OCR/table work as one conversion call in this pinned integration, so `docling_pipeline_conversion` is reported as a combined stage rather than splitting unobservable internal timings.

| Fixture | Run | Page | Child wall (ms) | Measured stage timings (ms) |
|---|---|---:|---:|---|
| `native-two-pages.pdf` | cold | 1 | 14988 | `{"anchor_and_evidence_assembly": 0.438, "docling_pipeline_conversion": 8820.572, "resource_verification_and_runtime_setup": 4367.523, "source_metadata_inventory": 39.011}` |
| `native-two-pages.pdf` | cold | 2 | 12232 | `{"anchor_and_evidence_assembly": 0.266, "docling_pipeline_conversion": 7645.619, "resource_verification_and_runtime_setup": 3167.592, "source_metadata_inventory": 33.502}` |
| `native-two-pages.pdf` | warm | 1 | 12675 | `{"anchor_and_evidence_assembly": 0.964, "docling_pipeline_conversion": 7845.68, "resource_verification_and_runtime_setup": 2912.922, "source_metadata_inventory": 30.805}` |
| `native-two-pages.pdf` | warm | 2 | 11915 | `{"anchor_and_evidence_assembly": 0.097, "docling_pipeline_conversion": 7566.584, "resource_verification_and_runtime_setup": 3194.273, "source_metadata_inventory": 40.944}` |
| `printed.jpeg` | cold | file | 12960 | `{"anchor_and_evidence_assembly": 0.2, "docling_pipeline_conversion": 8944.573, "resource_verification_and_runtime_setup": 2931.166, "source_metadata_inventory": 2.048}` |
| `printed.jpeg` | warm | file | 12625 | `{"anchor_and_evidence_assembly": 0.197, "docling_pipeline_conversion": 8672.064, "resource_verification_and_runtime_setup": 2690.576, "source_metadata_inventory": 1.938}` |
| `printed.jpg` | cold | file | 14292 | `{"anchor_and_evidence_assembly": 1.164, "docling_pipeline_conversion": 9650.808, "resource_verification_and_runtime_setup": 3263.555, "source_metadata_inventory": 2.118}` |
| `printed.jpg` | warm | file | 13392 | `{"anchor_and_evidence_assembly": 1.5, "docling_pipeline_conversion": 9178.077, "resource_verification_and_runtime_setup": 3053.325, "source_metadata_inventory": 2.427}` |
| `printed.png` | cold | file | 13591 | `{"anchor_and_evidence_assembly": 0.373, "docling_pipeline_conversion": 9245.139, "resource_verification_and_runtime_setup": 2816.455, "source_metadata_inventory": 11.374}` |
| `printed.png` | warm | file | 13913 | `{"anchor_and_evidence_assembly": 0.315, "docling_pipeline_conversion": 9297.988, "resource_verification_and_runtime_setup": 3039.035, "source_metadata_inventory": 12.314}` |
| `rotated.jpeg` | cold | file | 13774 | `{"anchor_and_evidence_assembly": 0.453, "docling_pipeline_conversion": 9264.368, "resource_verification_and_runtime_setup": 3016.537, "source_metadata_inventory": 26.18}` |
| `rotated.jpeg` | warm | file | 15145 | `{"anchor_and_evidence_assembly": 0.429, "docling_pipeline_conversion": 10453.722, "resource_verification_and_runtime_setup": 3118.823, "source_metadata_inventory": 27.748}` |
| `scanned.pdf` | cold | 1 | 14790 | `{"anchor_and_evidence_assembly": 0.22, "docling_pipeline_conversion": 9331.643, "resource_verification_and_runtime_setup": 3153.746, "source_metadata_inventory": 32.569}` |
| `scanned.pdf` | warm | 1 | 13866 | `{"anchor_and_evidence_assembly": 2.033, "docling_pipeline_conversion": 8889.612, "resource_verification_and_runtime_setup": 3542.891, "source_metadata_inventory": 34.143}` |

## Support boundaries

- OCR output is an extracted technical artifact, not verified handwriting recognition.
- Spreadsheet formulas are never executed; cached values remain unverified.
- Charts, drawings and embedded media are inventory items and remain unsupported unless the parser emits verified content.
- A successful sample is not evidence that every allowed 100-page or maximum-size bundle finishes within 600 active seconds.
- Docling 2.140.0 reports render/layout/OCR/table work as a combined conversion call; stage timings do not claim unexposed internal substage measurements.
- Provider spend is zero because no provider is invoked; Docker/host electricity or compute cost is not metered.
