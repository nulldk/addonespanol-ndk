---
title: Addonespanol Ndk
emoji: 🐳
colorFrom: purple
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

Check out the configuration reference at https://huggingface.co/docs/hub/spaces-config-reference

## Configuration

Put secrets and hostnames in the environment or a local `.env` file. `.env` is gitignored. Do not commit real values.

`NAS_BASE_URL` is the base URL of the catalog NAS, with no trailing slash. After a database refresh the app requests `{NAS_BASE_URL}/updatedb`. If the variable is unset, that call is skipped.

```
NAS_BASE_URL=https://nas.example.invalid
```

These other values are also read from the environment (no defaults are stored in the repo):

- `DEBRID_API_KEY`
- `FICHIER_API_KEY` (one key, or several separated by commas)
- `ENCRYPTION_KEY_B64`
- `ZIP_DECODE_BASE64`
- `RENDER_API_KEY`
- `RENDER_DEPLOY_HOOK`
- `ADMIN_PATH_DB_ENCRYPTED`
- `ADMIN_PATH_DB_DECRYPTED`
- `ADMIN_PATH_RESTART`
