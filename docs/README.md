# docs — Document Corpus

This directory contains the document corpus that the agent searches in **sample** mode.

The files are plain text so they stay easy to read and review. SharePoint knowledge sources in
Copilot Studio accept Office formats and PDF but not `.txt`, so `scripts/build_corpus.py` converts
every `.txt` here to a `.docx` (same name, same subfolders) before upload. Citations therefore name
the `.docx`: `lockout_tagout_procedure.txt` is cited as `lockout_tagout_procedure.docx`.

Documents remain the source of truth for answers and citations. Sample mode makes them readable by
whoever is a member of the knowledge site, so do not put restricted documents here. For internal
knowledge bases use [protected mode](../guides/purview.md).

## Default content

The default docs are sample manufacturing operations documents, the same set the Azure template uses:

- `safety/` — Safety procedures (e.g., lockout/tagout)
- `maintenance/` — Equipment maintenance manuals
- `quality/` — Quality control standards

## Replacing with your own documents

1. Delete the files in this directory (keep the directory structure or create your own).
2. Add your own `.txt` files in any subdirectory structure, or skip this directory and upload
   `.docx`, `.pdf`, `.pptx` or `.xlsx` files to the SharePoint library directly.
3. Update the agent instructions in `agent/agent.mcs.yml` to match your domain.
4. Update `tests/eval_cases.json` so the expected sources match your files (names end in `.docx`).
5. Run `bash scripts/provision.sh` to rebuild and re-upload.

## Tips

- Organize documents in subdirectories by category; folder names appear in the library.
- Keep individual documents focused on a single topic for better retrieval quality.
- SharePoint search must index new files before the agent can find them: allow 20 to 30 minutes for a
  new site, less for new files in an existing one.
