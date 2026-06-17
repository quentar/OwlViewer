# Packaging OwletMonitor

These scripts build a copy-and-run desktop app with PyInstaller. Build each platform on that platform.

## macOS

From the `pyowletapi` folder:

```bash
chmod +x dist-scripts/build-macos.sh dist-scripts/make-login-template.sh
dist-scripts/build-macos.sh
dist-scripts/make-login-template.sh
```

Output:

```text
dist/OwletMonitor.app
dist/login.json.template
```

Rename `login.json.template` to `login.json`, fill credentials, and keep it next to `OwletMonitor.app`.

## Windows

From PowerShell in the `pyowletapi` folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\dist-scripts\build-windows.ps1
```

Output:

```text
dist\OwletMonitor\OwletMonitor.exe
```

Create `dist\OwletMonitor\login.json` next to the exe:

```json
{
  "region": "world",
  "username": "you@example.com",
  "password": "your_password"
}
```

## Runtime Files

The bundled app contains a default `layout.json`. On first launch, it copies that file next to the app/exe and then saves user settings there.

Expected macOS distribution layout:

```text
OwletMonitor.app
login.json
layout.json
```

Expected Windows distribution layout:

```text
OwletMonitor.exe
login.json
layout.json
```

`layout.json` is created automatically if missing. `login.json` must be supplied by the user.
