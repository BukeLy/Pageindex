# Full Workspace Layer Research Summary

| strategy | hit rate | timeout | wrong | avg sec | tool calls | cat | grep | find | output chars |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_existing_source_grep | 0.000 | 3 | 0 | 76.8 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |
| folder_semantic | 0.000 | 0 | 0 | 8.2 | 0.0 | 0.0 | 0.0 | 0.0 | 0 |

## Failures

### baseline_existing_source_grep
- qst_0007 sources=google_drive error=TimeoutError: MaxSecondsExceeded: exceeded 60s got= expected=dsid_72ec4a9962ba43e88acd61abbba1052d
- qst_0009 sources=gmail error=TimeoutError: MaxSecondsExceeded: exceeded 60s got= expected=dsid_85deb10a652742baaf28af6149600001
- qst_0011 sources=confluence error=TimeoutError: MaxSecondsExceeded: exceeded 60s got= expected=dsid_46a4cb87db414e769f2df86f01626948

### folder_semantic
- qst_0007 sources=google_drive error=APIConnectionError: Connection error. got= expected=dsid_72ec4a9962ba43e88acd61abbba1052d
- qst_0009 sources=gmail error=APIConnectionError: Connection error. got= expected=dsid_85deb10a652742baaf28af6149600001
- qst_0011 sources=confluence error=APIConnectionError: Connection error. got= expected=dsid_46a4cb87db414e769f2df86f01626948

