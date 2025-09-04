import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
import json
import signal
import sys

import yfinance as yf
import pandas as pd
from telegram import Bot, Update
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

            if os.path.exists('watched_stocks.json'):
                with open('watched_stocks.json', 'r') as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
            logger.warning(f"Could not load custom stocks config: {e}")
            
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
                'current_price': round(float(current_price), 2),
                'previous_close': round(float(previous_close), 2),
                'pct_change': round(float(pct_change), 2),
                'volume': int(volume),
                'avg_volume': int(avg_volume),
                'ma_20': round(float(ma_20), 2),
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

        return f"""{severity}

📈 *{stock_info['name']} ({symbol})*

💰 Current Price: ${data['current_price']}
📊 Change: {data['pct_change']:+.2f}%
📅 Previous Close: ${data['previous_close']}

📋 *Analysis:*
• 20-day MA: ${data['ma_20']} ({ma_distance:+.1f}% from MA)
• Volume: {data['volume']:,} ({volume_vs_avg:.1f}x avg)
• Alert Threshold: {stock_info['threshold']}%

🕐 *Time:* {data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}"""

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
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.monitor = StockDipMonitor(bot_token, chat_id)
        self.app = Application.builder().token(bot_token).build()
        self.setup_handlers()
        self.is_checking = False
        self.running = True

    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("add", self.add_stock_command))
        self.app.add_handler(CommandHandler("remove", self.remove_stock_command))
        self.app.add_handler(CommandHandler("list", self.list_stocks_command))
        self.app.add_handler(CommandHandler("check", self.manual_check_command))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 Stock Dip Alert Bot Started!\n\n"
            "Commands:\n"
            "• /status - Check bot status\n"
            "• /list - List monitored stocks\n"
            "• /add SYMBOL THRESHOLD NAME - Add stock\n"
            "• /remove SYMBOL - Remove stock\n"
            "• /check - Manual stock check"
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        watched_count = len(self.monitor.watched_stocks)
        status = "Checking..." if self.is_checking else "Active"
        await update.message.reply_text(
            f"📊 Bot Status\n\n"
            f"Monitoring: {watched_count} stocks\n"
            f"Last check: {datetime.now().strftime('%H:%M:%S')}\n"
            f"Status: {status}"
        )

    async def list_stocks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.monitor.watched_stocks:
            await update.message.reply_text("No stocks being monitored.")
            return
        message = "📋 Watched Stocks:\n\n"
        for symbol, info in self.monitor.watched_stocks.items():
            message += f"• {symbol} - {info['name']} (Threshold: {info['threshold']}%)\n"
        await update.message.reply_text(message)

    async def add_stock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await update.message.reply_text("❌ Invalid threshold value.")

    async def remove_stock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /remove SYMBOL")
            return
        symbol = context.args[0].upper()
        if symbol in self.monitor.watched_stocks:
            del self.monitor.watched_stocks[symbol]
            self.monitor.save_watched_stocks(self.monitor.watched_stocks)
            await update.message.reply_text(f"✅ Removed {symbol}")
        else:
            await update.message.reply_text(f"❌ {symbol} not found in watched list")

    async def manual_check_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.is_checking:
            await update.message.reply_text("🔄 Already checking stocks...")
            return
        await update.message.reply_text("🔍 Checking stocks manually...")
        self.is_checking = True
        try:
            await self.monitor.check_stocks()
            await update.message.reply_text("✅ Manual check completed")
        except Exception as e:
            await update.message.reply_text(f"❌ Error during check: {str(e)}")
        finally:
            self.is_checking = False

    async def periodic_stock_check(self):
        """Background task for periodic stock monitoring"""
        logger.info("Starting periodic stock monitoring...")
        while self.running:
            try:
                await asyncio.sleep(900)  # Wait 15 minutes
                if not self.running:
                    break
                    
                if not self.is_checking:
                    self.is_checking = True
                    try:
                        logger.info("Running periodic stock check...")
                        await self.monitor.check_stocks()
                    except Exception as e:
                        logger.error(f"Error in periodic check: {e}")
                    finally:
                        self.is_checking = False
                        
            except asyncio.CancelledError:
                logger.info("Periodic check task was cancelled")
                break
            except Exception as e:
                logger.error(f"Error in periodic check loop: {e}")
                await asyncio.sleep(60)  # Wait a minute on error

    async def start_bot(self):
        """Start the bot with manual event loop management"""
        logger.info("Initializing bot...")
        
        # Initialize application
        await self.app.initialize()
        await self.app.start()
        
        # Start the periodic checking task
        check_task = asyncio.create_task(self.periodic_stock_check())
        
        try:
            logger.info("Starting polling...")
            # Start polling manually
            updater = self.app.updater
            await updater.start_polling(
                bootstrap_retries=5,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
            
            logger.info("Bot is running...")
            
            # Keep the bot running
            while self.running:
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"Error in bot polling: {e}")
        finally:
            logger.info("Shutting down bot...")
            self.running = False
            
            # Cancel the check task
            check_task.cancel()
            try:
                await check_task
            except asyncio.CancelledError:
                pass
            
            # Stop the updater and application
            try:
                await updater.stop()
            except:
                pass
            await self.app.stop()
            await self.app.shutdown()

    def stop_bot(self):
        """Signal the bot to stop"""
        logger.info("Stop signal received")
        self.running = False


def setup_signal_handlers(bot):
    """Setup signal handlers for graceful shutdown"""
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping bot...")
        bot.stop_bot()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


async def main():
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required")
        sys.exit(1)
        
    if not CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID environment variable is required")
        sys.exit(1)

    try:
        bot = TelegramStockBot(BOT_TOKEN, CHAT_ID)
        setup_signal_handlers(bot)
        await bot.start_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application interrupted")
    except Exception as e:
        logger.error(f"Application failed: {e}")
        sys.exit(1)
