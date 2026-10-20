"""Support ``python -m credit_engine`` as well as the installed entry point."""
from .cli import main

raise SystemExit(main())
