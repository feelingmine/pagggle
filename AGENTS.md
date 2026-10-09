# Pagggle development rules

- Product baseline: `docs/product/外贸独立站 AI 内容运营 PRD V1.0.md`. Follow P0 and M1 → M2 → M3; do not silently change the PRD.
- Keep product documents in `docs/product/`, engineering decisions and verification in `docs/development/`, Python application and tests in `backend/`, browser assets in `frontend/`.
- All local settings belong in root `config.json`. Never print, commit, serve, or place its secrets in prompts. Only `config.example.json` is tracked.
- Runtime databases, fetched sources and customer materials belong in ignored `data/`. Every customer record and query must be scoped by project ID.
- Develop and verify one step at a time. Record actual verification and limitations. Commit each verified step separately when Git writes are permitted; never claim a blocked commit succeeded.
- Keep changes minimal. Preserve original documents, confirmed evidence and manual edits. Unknown facts remain unknown.
- This first pilot is local-only. Project scoping is not authentication or multi-tenant authorization.
- Fixed verification data (user-selected replacement, 2026-10-09): use only `https://siliconeproductsmanufacturer.com/` and `backend/tests/data/silicone-baby-products_all-keywords_us_2026-10-09.xlsx`. The user explicitly authorized clearing the former supplier import/analysis and replacing it with this file; the old baseline remains in backups. Reuse existing website analysis; do not rerun it without an explicit request. Do not invent, replace, or modify test data or create synthetic verification projects. Any additional dataset requires user agreement first.
- Use the original website project on `http://127.0.0.1:8000` with `data/pagggle.sqlite3` for product verification. Do not restart the obsolete port-8001 synthetic verification environment or switch the user to it. Preserve existing records and manual edits; reuse an existing matching keyword import. See `docs/development/fixed-verification-data.md` for the verified baseline.
