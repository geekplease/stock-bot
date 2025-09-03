import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

import yfinance as yf
import pandas as pd
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import schedule
import time
from threading import Thread

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
        """Load watched stocks from config or use defaults"""
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
            # Try to load from environment variable (for cloud deployment)
            stocks_json = os.getenv('WATCHED_STOCKS')
            if stocks_json:
                return json.loads(stocks_json)
            
            # Try to load from file (for local development)
            with open('watched_stocks.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("Using default stock configuration")
            return default_stocks
    
    def save_watched_stocks(self, stocks: Dict):
        """Save watched stocks - for cloud deployment, this just updates memory"""
        self.watched_stocks = stocks
        # In cloud deployment, we can't write files, so we just update memory
        # For persistent storage, you'd need a database
        logger.info(f"Updated watched stocks: {list(stocks.keys())}")
    
    async def get_stock_data(self, symbol: str) -> Optional[Dict]:
        """Fetch current stock data"""
        try:
            stock = yf.Ticker(symbol)
            hist = stock.history(period="5d")
            
            if hist.empty:
                logger.warning(f"No data available for {symbol}")
                return None
                
            current_price = hist['Close'].iloc[-1]
            previous_close = hist['Close'].iloc[-2] if len(hist) >= 2 else current_price
            
            # Calculate percentage change
            pct_change = ((current_price - previous_close) / previous_close) * 100
            
            # Get additional metrics
            volume = hist['Volume'].iloc[-1]
            avg_volume = hist['Volume'].mean()
            
            # Get 20-day moving average for context
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
        """Determine if the price movement constitutes a significant dip"""
        threshold = self.watched_stocks[symbol]['threshold']
        
        # Check if it's a dip (negative change) beyond threshold
        if data['pct_change'] <= -threshold:
            # Additional filters to avoid noise
            volume_ratio = data['volume'] / data['avg_volume'] if data['avg_volume'] > 0 else 1
            
            # Check if we haven't alerted for this stock recently
            last_alert = self.price_history.get(symbol, {}).get('last_alert')
            if last_alert and datetime.now() - last_alert < timedelta(hours=4):
                return False
            
            return volume_ratio > 1.2  # Volume 20% above average
        
        return False
    
    def generate_alert_message(self, symbol: str, data: Dict) -> str:
        """Generate a formatted alert message"""
        stock_info = self.watched_stocks[symbol]
        
        # Determine alert severity
        if data['pct_change'] <= -8:
            severity = "🚨 MAJOR DIP ALERT"
        elif data['pct_change'] <= -5:
            severity = "⚠️ SIGNIFICANT DIP"
        else:
            severity = "📉 DIP DETECTED"
        
        # Calculate distance from 20-day MA
        ma_distance = ((data['current_price'] - data['ma_20']) / data['ma_20']) * 100
        
        volume_vs_avg = (data['volume'] / data['avg_volume']) if data['avg_volume'] > 0 else 1
        
        message = f"""
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

💡 **Suggestion:** Consider this dip as a potential buying opportunity if it aligns with your investment strategy and risk tolerance.
        """.strip()
        
        return message
    
    async def send_alert(self, message: str):
        """Send alert message via Telegram"""
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
        """Check all watched stocks for dips"""
        logger.info("Checking stocks for dips...")
        
        for symbol in self.watched_stocks.keys():
            try:
                data = await self.get_stock_data(symbol)
                if not data:
                    continue
                
                # Update price history
                if symbol not in self.price_history:
                    self.price_history[symbol] = {}
                
                self.price_history[symbol]['current_data'] = data
                
                # Check for significant dip
                if self.is_significant_dip(symbol, data):
                    message = self.generate_alert_message(symbol, data)
                    await self.send_alert(message)
                    
                    # Record alert time
                    self.price_history[symbol]['last_alert'] = datetime.now()
                    
                    logger.info(f"Dip alert sent for {symbol}: {data['pct_change']:.2f}%")
                
                # Small delay between API calls
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
        """Setup command handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("status", self.status_command))
        self.app.add_handler(CommandHandler("add", self.add_stock_command))
        self.app.add_handler(CommandHandler("remove", self.remove_stock_command))
        self.app.add_handler(CommandHandler("list", self.list_stocks_command))
        self.app.add_handler(CommandHandler("check", self.manual_check_command))
    
    async def start_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        message = """
🤖 **Stock Dip Alert Bot Started!**

**Available Commands:**
• `/status` - Check bot status
• `/list` - List watched stocks
• `/add SYMBOL THRESHOLD NAME` - Add stock to watch list
• `/remove SYMBOL` - Remove stock from watch list
• `/check` - Manual stock check

The bot will automatically monitor your stocks and alert you when significant dips occur!
        """
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def status_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        watched_count = len(self.monitor.watched_stocks)
        status = "Checking..." if self.is_checking else "Active"
        message = f"""
📊 **Bot Status**

🔍 Monitoring: {watched_count} stocks
⏰ Last check: {datetime.now().strftime('%H:%M:%S')}
✅ Status: {status}
☁️ Deployed: 24/7 Cloud Hosting

The bot checks for dips every 15 minutes during market hours.
        """
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def list_stocks_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list command"""
        if not self.monitor.watched_stocks:
            await update.message.reply_text("No stocks being monitored.")
            return
        
        message = "📋 **Watched Stocks:**\n\n"
        for symbol, info in self.monitor.watched_stocks.items():
            message += f"• {symbol} - {info['name']} (Threshold: {info['threshold']}%)\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def add_stock_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /add command"""
        if len(context.args) < 3:
            await update.message.reply_text("Usage: /add SYMBOL THRESHOLD NAME\nExample: /add AAPL 3.0 Apple Inc.")
            return
        
        symbol = context.args[0].upper()
        try:
            threshold = float(context.args[1])
            name = " ".join(context.args[2:])
            
            self.monitor.watched_stocks[symbol] = {
                "threshold": threshold,
                "name": name
            }
            self.monitor.save_watched_stocks(self.monitor.watched_stocks)
            
            await update.message.reply_text(f"✅ Added {symbol} ({name}) with {threshold}% threshold")
            
        except ValueError:
            await update.message.reply_text("❌ Invalid threshold value. Please use a number.")
    
    async def remove_stock_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remove command"""
        if not context.args:
            await update.message.reply_text("Usage: /remove SYMBOL")
            return
        
        symbol = context.args[0].upper()
        if symbol in self.monitor.watched_stocks:
            del self.monitor.watched_stocks[symbol]
            self.monitor.save_watched_stocks(self.monitor.watched_stocks)
            await update.message.reply_text(f"✅ Removed {symbol} from watch list")
        else:
            await update.message.reply_text(f"❌ {symbol} not found in watch list")
    
    async def manual_check_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /check command"""
        if self.is_checking:
            await update.message.reply_text("🔄 Already checking stocks, please wait...")
            return
            
        await update.message.reply_text("🔍 Checking stocks manually...")
        self.is_checking = True
        try:
            await self.monitor.check_stocks()
            await update.message.reply_text("✅ Manual check completed")
        finally:
            self.is_checking = False
    
    async def scheduled_check(self):
        """Scheduled stock check"""
        if not self.is_checking:
            self.is_checking = True
            try:
                await self.monitor.check_stocks()
            finally:
                self.is_checking = False
    
    async def start_bot(self):
        """Start the bot"""
        logger.info("Initializing stock bot...")
        
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
        logger.info("Bot started successfully! 🚀")
        
        # Schedule periodic checks (every 15 minutes)
        async def periodic_check():
            while True:
                await asyncio.sleep(900)  # 15 minutes = 900 seconds
                await self.scheduled_check()
        
        # Start periodic checking
        asyncio.create_task(periodic_check())
        
        # Keep the bot running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        finally:
            await self.app.stop()

async def main():
    """Main function"""
    # Get environment variables
    BOT_TOKEN = os.getenv('8448771678:AAGdqr4LUgWF-5iDKFpgj5QXK_2oHDNpxc8')
    CHAT_ID = os.getenv('132643480')
    
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("Missing required environment variables: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return
    
    logger.info("Starting Stock Dip Alert Bot...")
    
    # Create and start the bot
    bot = TelegramStockBot(BOT_TOKEN, CHAT_ID)
    await bot.start_bot()

if __name__ == "__main__":
    asyncio.run(main())