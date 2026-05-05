"""Bundled templates that ship with the installed package.

The repo's `.env.example` lives at the root for git-based workflows
(manual clone, curl|bash install) where it's directly readable. For
pipx installs, where only declared package files are shipped, we
mirror it here as `env_example.txt` so the wizard can render the
same template in both contexts.

Loader: see `wizard.py:_read_env_template()` for the resolution order.
"""
