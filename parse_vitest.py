import json, sys

with open('/tmp/vitest_results.json') as fh:
    d = json.load(fh)

passed = failed = 0
for f in d['testResults']:
    for t in f['assertionResults']:
        if t['status'] == 'passed':
            passed += 1
        else:
            failed += 1
            fname = list(f.values())[0] if isinstance(list(f.values())[0], str) else str(f)
            # Try to get a filename
            for key in ['testFilePath', 'name', 'file']:
                if key in f:
                    fname = f[key].split('\\')[-1].split('/')[-1]
                    break
            print(f"FILE: {fname}")
            print(f"TEST: {t['fullName']}")
            if t.get('failureMessages'):
                print(f"ERROR: {t['failureMessages'][0][:600]}")
            print()

print(f"Total: {passed+failed} | Passed: {passed} | Failed: {failed}")
