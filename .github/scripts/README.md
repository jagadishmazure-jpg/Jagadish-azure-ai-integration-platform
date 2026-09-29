# `.github/scripts`

Shell steps called by the deploy and teardown workflows, kept out of YAML so they can be read, linted and run by hand.

| File | What it does |
|---|---|
| [`deploy.sh`](deploy.sh) | `provision` (Terraform apply or Bicep `az deployment sub create`), `push` / `import` the platform image, `roll` every Container App, `functions` zip-deploy, `smoke` test the tool gateway `/healthz`, `destroy`. |
