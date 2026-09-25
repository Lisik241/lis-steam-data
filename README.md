# lis-steam-data

Steam data automation for the account configured in `steam_account.json`.

Based on the automation structure of `Karai-hub/karai-steam-data`.

## Current state

The first Steam data update completed successfully and generated the library, wishlist, and DLC snapshot files.

## Configuration

- `steam_account.json` is the single source of truth for the SteamID.
- GitHub Actions uses the repository secret `STEAM_API_KEY`.
- Store region and language are currently supplied by the update workflow.

## Automation

- `Update Steam data` refreshes account data daily and can also be run manually.
- `Scan Steam giveaways` checks Steam giveaways every four hours.
- Regression tests run before account data is refreshed.

## Known limitation

The first run found zero DLC relations for all 78 library entries. The empty DLC catalog should therefore be treated as incomplete until a reliable fallback discovery method is added.
