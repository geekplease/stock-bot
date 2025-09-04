

import asyncio import logging import os from datetime import datetime, timedelta from typing import Dict, Optional import json import yfinance as yf import pandas as pd from telegram import Bot from telegram.ext import Application, CommandHandler, ContextTypes # Configure logging logging.basicConf

pasted

this py file is not deploying giving Updater error. I've tried different solutions but it's not working:

Looking at your Telegram bot code, the issue is likely related to how you're trying to run the bot. The error you're seeing is probably because you're using run_polling() which creates its own event loop, but you're also trying to run a concurrent periodic task.

Here's the corrected version that should resolve the deployment issues:

Fixed Telegram Stock Bot
Code 
The main changes I made to fix the deployment issues:

Key Fixes:
Proper Application Lifecycle Management: Added explicit initialization, start, and cleanup phases for the Telegram application.
Correct Polling Setup: Used app.updater.start_polling() instead of app.run_polling() to have better control over the event loop.
Task Management: Properly handle the periodic checking task with cancellation support.
Better Error Handling: Added comprehensive try-catch blocks around critical sections.
Graceful Shutdown: Implemented proper cleanup sequence when the bot is stopped.
Additional Recommendations:
Requirements.txt: Make sure you have this file with:
python-telegram-bot>=20.0
yfinance>=0.2.0
pandas>=1.5.0
Environment Variables: Ensure these are set:
TELEGRAM_BOT_TOKEN: Your bot token from BotFather
TELEGRAM_CHAT_ID: Your chat ID (you can get this by sending a message to your bot and checking https://api.telegram.org/bot<token>/getUpdates)
For Cloud Deployment (like Heroku, Railway, etc.), you might also need a Procfile:
worker: python main.py
The main issue with your original code was trying to use run_polling() while also creating concurrent tasks, which creates conflicting event loops. This fixed version properly manages the asyncio event loop and task lifecycle.






import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
import json

import yfinance as yf
import pandas as pd
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class StockDipMonitor:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot = Bot(token=bot_token)
        self.watched_stocks = self.load_watched_stocks()
        self.price_history = {}

    def load_watched_stocks(self) -> Dict:
        default_stocks = {
            "AAPL": {"threshold": 3.0, "name": "Apple Inc."},
            "GOOGL": {"threshold": 3.0, "name": "Alphabet Inc."},
            "MSFT": {"threshold": 3.0, "name": "Microsoft Corp."},
            "TSLA": {"threshold": 5.0, "name": "Tesla Inc."},
            "NVDA": {"threshold": 5.0, "name": "NVIDIA Corp."},
            "AMZN": {"threshold": 3.0, "name": "Amazon.com Inc."},
            "META": {"threshold": 4.0, "name": "Meta Platforms Inc."}
        }

        try:
            stocks_json = os.getenv('WATCHED_STOCKS')
            if stocks_json:
                return json.loads(stocks_json)

            with open('watched_stocks.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("Using default stock configuration")
            return default_stocks

    def save_watched_stocks(self, stocks: Dict):
        self.watched_stocks = stocks
        logger.info(f"Updated watched stocks: {list(stocks.keys())}")

    async def get_stock_data(self, symbol: str) -> Optional[Dict]:
        try:
            stock = yf.Ticker(symbol)
            hist = stock.history(period="5d")

            if hist.empty:
                logger.warning(f"No data available for {symbol}")
                return None

            current_price = hist['Close'].iloc[-1]
            previous_close = hist['Close'].iloc[-2] if len(hist) >= 2 else current_price
            pct_change = ((current_price - previous_close) / previous_close) * 100

            volume = hist['Volume'].iloc[-1]
            avg_volume = hist['Volume'].mean()

            hist_20d = stock.history(period="1mo")
            ma_20 = hist_20d['Close'].rolling(window=20).mean().iloc[-1] if len(hist_20d) >= 20 else current_price

            return {
                'symbol': symbol,
                'current_price': round(current_price, 2),
                'previous_close': round(previous_close, 2),
                'pct_change': round(pct_change, 2),
                'volume': int(volume),
                'avg_volume': int(avg_volume),
                'ma_20': round(ma_20, 2),
                'timestamp': datetime.now()
            }

        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            return None

    def is_significant_dip(self, symbol: str, data: Dict) -> bool:
        threshold = self.watched_stocks[symbol]['threshold']

        if data['pct_change'] <= -threshold:
            volume_ratio = data['volume'] / data['avg_volume'] if data['avg_volume'] > 0 else 1
            last_alert = self.price_history.get(symbol, {}).get('last_alert')
            if last_alert and datetime.now() - last_alert < timedelta(hours=4):
                return False
            return volume_ratio > 1.2
        return False

    def generate_alert_message(self, symbol: str, data: Dict) -> str:
        stock_info = self.watched_stocks[symbol]

        if data['pct_change'] <= -8:
            severity = "🚨 MAJOR DIP ALERT"
        elif data['pct_change'] <= -5:
            severity = "⚠️ SIGNIFICANT DIP"
        else:
            severity = "📉 DIP DETECTED"

        ma_distance = ((data['current_price'] - data['ma_20']) / data['ma_20']) * 100
        volume_vs_avg = (data['volume'] / data['avg_volume']) if data['avg_volume'] > 0 else 1

        return f"""
{severity}

📈 **{stock_info['name']} ({symbol})**

💰 Current Price: ${data['current_price']}
📊 Change: {data['pct_change']:+.2f}%
📅 Previous Close: ${data['previous_close']}

📋 **Analysis:**
• 20-day MA: ${data['ma_20']} ({ma_distance:+.1f}% from MA)
• Volume: {data['volume']:,} ({volume_vs_avg:.1f}x avg)
• Alert Threshold: {stock_info['threshold']}%

🕐 **Time:** {data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}
        """.strip()

    async def send_alert(self, message: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            logger.info("Alert sent successfully")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    async def check_stocks(self):
        logger.info("Checking stocks...")
        for symbol in self.watched_stocks.keys():
            try:
                data = await self.get_stock_data(symbol)
                if not data:
                    continue
                if symbol not in self.price_history:
                    self.price_history[symbol] = {}
                self.price_history[symbol]['current_data'] = data

                if self.is_significant_dip(symbol, data):
                    message = self.generate_alert_message(symbol, data)
                    await self.send_alert(message)
                    self.price_history[symbol]['last_alert'] = datetime.now()
                    logger.info(f"Dip alert sent for {symbol}: {data['pct_change']:.2f}%")

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}")

        logger.info("Stock check completed")


class TelegramStockBot:
    def __init__(self, bot_token: str, chat_id: str):
        self.monitor = StockDipMonitor(bot_token, chat_id)
        self.app = Application.builder().token(bot_token).build()
        self.setup_handlers()
        self.is_checking = False

    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("add", self.add_stock_command))
        self.app.add_handler(CommandHandler("remove", self.remove_stock_command))
        self.app.add_handler(CommandHandler("list", self.list_stocks_command))
        self.app.add_handler(CommandHandler("check", self.manual_check_command))

    async def start_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 Stock Dip Alert Bot Started!\n\n"
            "Commands:\n"
            "• /status\n"
            "• /list\n"
            "• /add SYMBOL THRESHOLD NAME\n"
            "• /remove SYMBOL\n"
            "• /check"
        )

    async def status_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        watched_count = len(self.monitor.watched_stocks)
        status = "Checking..." if self.is_checking else "Active"
        await update.message.reply_text(
            f"📊 Bot Status\n\n"
            f"Monitoring: {watched_count} stocks\n"
            f"Last check: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Status: {status}\n"
        )

    async def list_stocks_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        if not self.monitor.watched_stocks:
            await update.message.reply_text("No stocks being monitored.")
            return
        message = "📋 Watched Stocks:\n\n"
        for symbol, info in self.monitor.watched_stocks.items():
            message += f"• {symbol} - {info['name']} (Threshold: {info['threshold']}%)\n"
        await update.message.reply_text(message)

    async def add_stock_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) < 3:
            await update.message.reply_text("Usage: /add SYMBOL THRESHOLD NAME")
            return
        symbol = context.args[0].upper()
        try:
            threshold = float(context.args[1])
            name = " ".join(context.args[2:])
            self.monitor.watched_stocks[symbol] = {"threshold": threshold, "name": name}
            self.monitor.save_watched_stocks(self.monitor.watched_stocks)
            await update.message.reply_text(f"✅ Added {symbol} ({name})")
        except ValueError:
            await update.message.reply_text("❌ Invalid threshold.")

    async def remove_stock_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /remove SYMBOL")
            return
        symbol = context.args[0].upper()
        if symbol in self.monitor.watched_stocks:
            del self.monitor.watched_stocks[symbol]
            self.monitor.save_watched_stocks(self.monitor.watched_stocks)
            await update.message.reply_text(f"✅ Removed {symbol}")
        else:
            await update.message.reply_text(f"❌ {symbol} not found")

    async def manual_check_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        if self.is_checking:
            await update.message.reply_text("🔄 Already checking...")
            return
        await update.message.reply_text("🔍 Checking stocks manually...")
        self.is_checking = True
        try:
            await self.monitor.check_stocks()
            await update.message.reply_text("✅ Manual check done")
        finally:
            self.is_checking = False

    async def scheduled_check(self):
        """Periodic stock checking function"""
        if not self.is_checking:
            self.is_checking = True
            try:
                await self.monitor.check_stocks()
            except Exception as e:
                logger.error(f"Error in scheduled check: {e}")
            finally:
                self.is_checking = False

    async def start_bot(self):
        """Start the bot with proper initialization and cleanup"""
        logger.info("Starting bot...")
        
        # Initialize the application
        await self.app.initialize()
        await self.app.start()
        
        # Start periodic checking task
        async def periodic_check():
            while True:
                try:
                    await asyncio.sleep(900)  # 15 minutes
                    await self.scheduled_check()
                except asyncio.CancelledError:
                    logger.info("Periodic check task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in periodic check task: {e}")

        # Create and start the periodic task
        check_task = asyncio.create_task(periodic_check())
        
        try:
            # Start polling
            await self.app.updater.start_polling()
            
            # Keep the bot running
            while True:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            # Cleanup
            check_task.cancel()
            try:
                await check_task
            except asyncio.CancelledError:
                pass
            
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


async def main():
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

    if not BOT_TOKEN or not CHAT_ID:
        logger.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID environment variables")
        return

    bot = TelegramStockBot(BOT_TOKEN, CHAT_ID)
    try:
        await bot.start_bot()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
