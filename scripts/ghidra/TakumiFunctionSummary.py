# Ghidra Jython-compatible, opt-in fixed summary script.
# @category Takumi
# No network, file writes, arbitrary model-provided script or private bytes.
args = getScriptArgs()
try:
    maximum = min(60, max(1, int(args[0]))) if args else 60
except Exception:
    maximum = 60
count = 0
try:
    fm = currentProgram.getFunctionManager()
    functions = fm.getFunctions(True)
    while functions.hasNext() and count < maximum:
        fn = functions.next()
        address = fn.getEntryPoint().toString()
        name = fn.getName().replace('|', '_').replace('\n', '_')[:100]
        print('TAKUMI_FUNCTION|' + address + '|' + name)
        count += 1
    print('TAKUMI_SUMMARY_COUNT|' + str(count))
except Exception as error:
    print('TAKUMI_ANALYSIS_ERROR|' + str(error)[:180])
