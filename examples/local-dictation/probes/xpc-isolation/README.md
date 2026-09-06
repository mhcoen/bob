# XPC isolation probe

The probe builds an unsandboxed app with a bundled sandboxed XPC service. It
compares workers with the network client entitlement withheld and granted.
Connections target a listener created by the app on the loopback interface.
No external service is contacted.

Run on macOS with Xcode command-line tools and Python 3:

```sh
python3 examples/local-dictation/probes/xpc-isolation/run.py
```

Each worker receives five synthetic bytes through an XPC file handle and allocates
a Metal buffer. A missing reply or unexpected result causes a nonzero exit.
The script uses temporary build directories and ad hoc code signatures. macOS
may retain the service's sandbox container after the temporary app is removed.

Observed on September 6, 2026, macOS 26.6.2 (25G83), Apple Silicon:

| Operation | Network entitlement withheld | Network entitlement granted |
| --- | --- | --- |
| App connects to its listener | Success | Success |
| Worker connects to the same listener | Denied, `EPERM` | Success |
| Worker reads the synthetic message | Success | Success |
| Worker creates a Metal device and buffer | Success | Success |

The result supports selecting a service sandbox independent of the app's sandbox
status. It does not establish inference runtime compatibility or behavior on an
earlier macOS version. Model file access needs a separate check. A Metal buffer
allocation does not exercise inference kernels. Release signing also needs testing.

The service is probe code. A shipping service needs caller authentication and an
IPC contract that validates requests before opening model files or starting work.
