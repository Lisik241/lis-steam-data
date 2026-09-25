# lis-steam-data

Steam data automation for SteamID `76561199832779057`.

Based on the automation structure of `Karai-hub/karai-steam-data`.

## Setup

Add a GitHub Actions repository secret named `STEAM_API_KEY`.
The `Update Steam data` workflow can then be run manually and also runs on its schedule.

The first Steam data update completed successfully. Generated account data is refreshed automatically by GitHub Actions.

Configuration lives in `steam_account.json`; the updater reads the SteamID from that file instead of duplicating it in code.

The giveaway hunter is installed, but personalized ranking should not be treated as meaningful until a `taste_profile.json` for this account is added.
