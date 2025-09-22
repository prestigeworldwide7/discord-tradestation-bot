# Discord TradeStation Bot

This repository contains a simple Python bot that listens for trade alerts in a Discord channel and automatically submits corresponding bracket orders to your TradeStation brokerage account.  The goal of this project is to demonstrate how to tie together a message stream (Discord) with a brokerage API (TradeStation) in a safe and configurable way.

## Features

- **Discord integration** – a small client built on top of the [`discord.py`](https://discordpy.readthedocs.io/) library connects to a single channel and listens for new messages.
- **Message parsing** – the bot expects messages in a specific format such as:

  ```
  AAPL - $250 CALLS EXPIRATION 10/10 $1.29 STOP LOSS AT $1.00
  ```

  It will extract the symbol, strike price, option type (call or put), expiration date, entry price and stop‐loss price from the message.
- **TradeStation integration** – once a valid message is parsed, the bot submits a bracket order via the TradeStation REST API.  A bracket order consists of a primary order to enter the position and a secondary stop‐loss order to exit the trade if it moves against you.
- **Configuration via environment variables** – all sensitive information (Discord token, TradeStation credentials) is provided via environment variables.  No secrets are stored in the repository.

## Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.  Before running the bot you should create a Discord bot, add it to your server, and register an application on the TradeStation developer portal.

### Prerequisites

* **Python 3.9+** – the bot is written for modern versions of Python.  You can install Python from [python.org](https://www.python.org/) or via your system package manager.
* **TradeStation developer account** – sign up on the [TradeStation developer portal](https://tradestation.github.io/) and create an application.  Record the client ID, client secret, redirect URI and request offline access to obtain a refresh token.
* **Discord bot token** – create a new bot on the [Discord Developer Portal](https://discord.com/developers/applications), enable the `message_content` intent, and invite the bot to the server/channel from which you receive trading alerts.

### Installation

Clone the repository and install the required Python packages using [`pip`](https://pip.pypa.io/):

```bash
git clone <your fork of this repository>
cd discord-tradestation-bot
pip install -r requirements.txt
```

### Configuration

The bot reads its configuration exclusively from environment variables.  You can define these variables in your shell or by creating a `.env` file in the project root (the provided `example.env` can be copied as a starting point).  The following variables are used:

| Variable             | Description                                                                                      |
|----------------------|--------------------------------------------------------------------------------------------------|
| `DISCORD_TOKEN`      | The token for your Discord bot (not your user token).                                             |
| `DISCORD_CHANNEL_ID` | The numeric ID of the Discord channel to monitor for alerts.                                       |
| `TS_CLIENT_ID`       | Your TradeStation application’s client ID.                                                        |
| `TS_CLIENT_SECRET`   | Your TradeStation application’s client secret.                                                    |
| `TS_ACCOUNT_KEY`     | The account key returned by the TradeStation API for your brokerage account.                      |
| `TS_REDIRECT_URI`    | The redirect URI registered with your TradeStation application.                                   |
| `TS_REFRESH_TOKEN`   | A refresh token generated via the OAuth2 authorization flow (grants permission to trade).         |
| `TS_BASE_URL`        | Base URL for the API; use `https://sim-api.tradestation.com/v3` for the simulator or             |
|                      | `https://api.tradestation.com/v3` for live trading.                                               |

**Never commit your secrets to source control.**  Environment variables allow you to keep credentials out of the repository.

### Running the Bot

After installing dependencies and configuring the environment variables, run the bot from the project root:

```bash
python main.py
```

The bot will connect to Discord and begin listening for messages in the specified channel.  When a message that matches the expected format arrives, the bot will parse the message and attempt to submit a bracket order via the TradeStation API.  All orders are placed using the account specified by `TS_ACCOUNT_KEY`.

### Notes and Limitations

* **Testing** – Always test your bot in the TradeStation simulator (`sim-api.tradestation.com`) before switching to the live API.  The simulator allows you to verify your logic without risking real funds.
* **Order validation** – This bot submits orders based on message content without additional validation.  You may wish to add checks to ensure you do not place orders that exceed your risk tolerance or account size.
* **Date parsing** – The expiration date parser assumes that dates without a year are in the future relative to the current date.  You can modify the date parsing logic in `main.py` to suit your needs.
* **No investment advice** – This code is provided for educational purposes only.  It does not constitute financial advice.  Trading involves risk, and you should consult a qualified professional before engaging in automated trading.

## Contributing

Feel free to fork this repository and submit pull requests.  If you find a bug or have ideas for improvement, please open an issue.

## License

This project is licensed under the MIT License – see the [LICENSE](LICENSE) file for details.
