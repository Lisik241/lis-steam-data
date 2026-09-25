# lis-steam-data

Steam data automation for SteamID `76561199832779057`.

Based on the automation structure of `Karai-hub/karai-steam-data`.

## Setup

Add a GitHub Actions repository secret named `STEAM_API_KEY`.
The `Update Steam data` workflow can then be run manually and also runs on its schedule.

Generated Steam account files are intentionally not copied from the source account.
They will be created from this Steam account after the first successful update.
