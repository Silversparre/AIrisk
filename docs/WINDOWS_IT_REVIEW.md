# AIrisk Windows package for IT review

The supplied x64 package includes Python and the application. End users do not need Python, pip, Node, Docker or WSL. It runs as the signed-in Windows user, opens their browser, and serves only `http://127.0.0.1:5000`. It does not install a service, add a firewall exception, edit the registry/trust store, bypass PowerShell policy or update itself. The executable is a console application: leave its window open, stop with Ctrl+C.

**Status: unsigned review build.** The organization has not yet supplied its signing identity or AppLocker/Windows App Control policy. A signature alone does not establish policy approval or guarantee absence of SmartScreen prompts. The portable folder can be evaluated on an IT-approved test machine; an MSIX must be signed and trusted before installation. Do not bypass a block on a managed endpoint.

## Contents and local use

- `AIrisk/AIrisk.exe` and `_internal/`: one-directory native Windows build. Keep the entire folder together. The interpreter, DLLs, templates, migrations and static assets are part of the application.
- Portable ZIP, optional unsigned MSIX, `build-info.json`, pinned `requirements.lock`, a CycloneDX **build-environment** inventory and dependency license files. The inventory includes build tools as well as runtime dependencies; it is not a claim that every listed package is loaded at runtime. File hashes enumerate actual distributed files.
- `SHA256SUMS.json` and `verify.py`: verify all extracted bundle files with an approved Python 3.11+ interpreter (`python verify.py`). Any missing, extra or modified file fails. SHA-256 hashes are not publisher authentication; obtain the manifest over a trusted channel. Do not modify the bundle in place after verification.

Default data location: `%LOCALAPPDATA%\AIrisk\instance`. The default data directory is outside the program folder. It contains `airisk.db`, `secret.key` and a transient server lock. Preserve it on upgrade; back it up according to corporate policy. No demo accounts or customer records are included in the distribution.

```powershell
.\AIrisk\AIrisk.exe
# Use a separate private directory/port for evaluation:
.\AIrisk\AIrisk.exe --instance 'C:\Users\YourName\AIrisk Review Data' --port 5050
# Make a verified SQLite online backup (destination must not exist):
.\AIrisk\AIrisk.exe backup 'C:\Users\YourName\Backups\airisk-20261108.db'
# Stop the server before administrator maintenance:
.\AIrisk\AIrisk.exe admin reset-password --email person@example.com
```

First startup displays/opens a private setup link. Create an administrator, then a separate reviewer in Team. There are no built-in credentials. Restarting preserves data and accounts; only one server may hold a data directory. For an existing source installation, back up and close its server, then pass `--instance 'D:\Path\AIrisk\instance'`. Do not copy its private data into the executable folder or review ZIP.

`--no-browser` suppresses browser opening; `--prepare-only` migrates and exits; `--version` prints the version. The local package rejects hosted `APP_ENV=production`/`DATABASE_URL` settings. Use the source deployment for a managed multi-user HTTPS server. Data/backup directories must be private local directories with suitable NTFS permissions and endpoint encryption. Uninstalling a package is not a backup strategy; MSIX profile virtualization may affect data retention, so IT must test upgrade/uninstall/restore under its chosen deployment method.

## Reproducible build inputs

Build on an approved **native Windows CPython 3.12 x64** installation. `packaging/windows/requirements.lock` pins every build/runtime wheel by version and SHA-256. A Python base-runtime update requires a new build and review. Build scripts require an approved Git executable on PATH for provenance. Do not build from a directory containing unreviewed source changes.

```powershell
python -m venv .venv-build-windows
.\.venv-build-windows\Scripts\python.exe -m pip download --only-binary=:all: --require-hashes -r packaging\windows\requirements.lock -d build\windows\wheels
.\.venv-build-windows\Scripts\python.exe -m pip install --no-index --find-links build\windows\wheels --require-hashes -r packaging\windows\requirements.lock
.\.venv-build-windows\Scripts\python.exe packaging\windows\build.py
```

Download from IT's approved package mirror; the offline install verifies hashes. Only the build machine downloads packages. Runtime startup never runs pip or downloads code. PyInstaller uses a directory build without UPX or one-file temporary extraction. See [PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html).

For MSIX, supply an approved Windows SDK `MakeAppx.exe` and the **exact Subject** of the intended signing certificate:

```powershell
.\.venv-build-windows\Scripts\python.exe packaging\windows\build.py --makeappx 'C:\Approved SDK\x64\makeappx.exe' --publisher 'CN=Your Organization, O=Your Organization, C=SE'
```

The builder refuses to overwrite an existing review artifact. Preserve or explicitly remove the old build before rebuilding. The default publisher `CN=AIrisk IT Review` is an unsigned placeholder, not a signing identity. Rebuild with the organization's subject before signing. The manifest uses desktop full trust at medium integrity, which does not request administrator elevation; IT must approve that capability. The technical manifest floor is Windows 10 build 19041, but deployment must use a Windows version supported under the organization's current servicing agreement. Validate each supported fleet image; an x64 build does not establish ARM64 compatibility.

Microsoft documents [manual MSIX manifests](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-manual-conversion) and [MakeAppx](https://learn.microsoft.com/en-us/windows/msix/package/create-app-package-with-makeappx-tool). The review build uses Microsoft.Windows.SDK.BuildTools 10.0.26100.9457 from NuGet; MakeAppx/SignTool Authenticode signatures are checked before use. SDK tooling is a build dependency, not shipped to end users.

## Signing and deployment

**Preferred managed distribution: sign the MSIX.** Its signature covers the package contents, including the bundled Python modules and resources. Signing `AIrisk.exe` alone does not cover the external DLLs/Python archives/templates in the portable folder. For portable distribution IT must protect the whole installed directory from ordinary-user modification and apply a policy covering all binaries/resources; a writable path allow rule is not an integrity boundary.

IT can use its existing code-signing service, certificate store or Microsoft Artifact Signing. No private keys/passwords belong in this repository. For a certificate already provisioned in the signing operator's Current User `My` store, the following is a template, with explicit certificate selection and an IT-approved RFC3161 timestamp service:

```powershell
signtool sign /sha1 CERTIFICATE_THUMBPRINT /s My /fd SHA256 /tr https://IT_APPROVED_TIMESTAMP_SERVICE /td SHA256 AIrisk-1.1.0-unsigned.msix
signtool verify /pa /all /v AIrisk-1.1.0-unsigned.msix
```

Use `/sm` only if IT intentionally provisions the certificate in the Local Machine store. A managed/cloud signer needs its provider-specific integration; the certificate-store example does not configure it. After signing, create a new final filename and release manifest/hashes: signing changes the artifact, so the unsigned manifest will no longer match. Retain the unsigned review evidence separately.

IT should validate publisher/chain/timestamp, approved distribution through Intune or its software channel, Defender scanning, AppLocker/Windows App Control behavior for the package and bundled interpreter, profile permissions, backup/restore and updates under a standard user. An ordinary desktop process with embedded Python has the user's permissions; it is not a sandbox for untrusted plugins. The app exposes no plugin execution feature. No certificate installation, policy exception or MSIX installation is performed by the build workflow.

References: [MSIX signing overview](https://learn.microsoft.com/en-us/windows/msix/package/signing-package-overview), [SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool), [packaged apps with Windows App Control](https://learn.microsoft.com/en-us/windows/security/threat-protection/windows-defender-application-control/manage-packaged-apps-with-windows-defender-application-control).
