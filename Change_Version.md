# Version Change Procedure
This document explains how to properly update, test, commit, and tag new versions of the `discogs-to-shopify` application.

Your working repo location:
C:\Users\unbre\OneDrive\Documents\GitHub\discogs-to-shopify

## 1. Before Making Any Changes
1. Open VS Code  
2. Use File → Open Folder… and open the repository folder.
3. Open a terminal: Terminal → New Terminal
4. Confirm Python is available:
   ```
   python --version
   ```
5. Confirm the app runs:
   ```
   python discogs_to_shopify_gui.py
   ```

## 2. Updating to a New Version
### Step 1 — Update the version constant
Edit APP_VERSION in discogs_to_shopify_gui.py

### Step 2 — Make code changes
Modify files as needed:
- discogs_to_shopify_gui.py
- discogs_to_shopify.py
- label_ocr.py

### Step 3 — Validate
```
python -m py_compile discogs_to_shopify_gui.py
python -m py_compile discogs_to_shopify.py
python discogs_to_shopify_gui.py
```

### Step 4 — Commit
Use VS Code Source Control, then Push via GitHub Desktop.

### Step 5 — Create GitHub Release & Tag
Tag format example: v1.2.4

## 3. If a Version Breaks
Use GitHub Releases → Browse files to restore prior version.

## 4. Optional Folder Structure Cleanup
```
src/
legacy_versions/
docs/
Change_Version.md
README.md
```

## 5. Quick Checklist
- Update APP_VERSION
- Make changes
- Compile test
- Run GUI test
- Commit
- Push
- Tag & Release
