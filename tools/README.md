# Developer and operator tools

- `build/`: frontend bundles and team preset builders.
- `diagnostics/`: inspect runtime data, including checkpoint conversation history.
- `maintenance/`: maintain repository documentation, including skill evolution.

These tools are invoked explicitly. Ordinary startup does not run them.
Frontend builds use `npm run build:pretext-vendor` and `npm run build:oasis-town`.
