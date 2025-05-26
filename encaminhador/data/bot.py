import os
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.error import Forbidden, BadRequest
from datetime import datetime, timedelta
import asyncio
from flask import Flask
from threading import Thread

# Configuração inicial
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estados da conversação
(
    SELECT_CHANNEL, SET_CHANNEL_TYPE, SET_CAPTION, 
    SET_SCHEDULE, SELECT_DAYS, SET_COOLDOWN,
    SET_MAX_DURATION
) = range(7)

# Configurações
CONFIG_FILE = 'bot_config.json'
DEFAULT_CONFIG = {
    'users': {},
    'global_settings': {
        'forwarding_enabled': True,
        'cooldown_seconds': 0,
        'max_duration': 0
    }
}

# Servidor Flask para manter o bot ativo no Replit
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de encaminhamento está online!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Funções auxiliares
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONFIG

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def get_user_config(user_id, config):
    user_id = str(user_id)
    if user_id not in config['users']:
        config['users'][user_id] = {
            'channels': {},
            'forwarding_enabled': True
        }
    return config['users'][user_id]

# Handlers de comando
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Configurar Canal", callback_data='setup_channel')],
        [InlineKeyboardButton("📋 Meus Canais", callback_data='list_channels')],
        [InlineKeyboardButton("⚙️ Configurações", callback_data='settings')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛠️ *Bot de Encaminhamento Automático*\n\n"
        "Selecione uma opção:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def setup_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "📤 Por favor, encaminhe uma mensagem do canal que deseja configurar "
            "ou envie o ID do canal (começando com -100).\n\n"
            "Use /cancelar para abortar."
        )
    else:
        await update.message.reply_text(
            "📤 Por favor, encaminhe uma mensagem do canal que deseja configurar "
            "ou envie o ID do canal (começando com -100).\n\n"
            "Use /cancelar para abortar."
        )
    return SELECT_CHANNEL

async def select_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.forward_from_chat:
        chat = update.message.forward_from_chat
        context.user_data['channel'] = {
            'id': str(chat.id),
            'title': chat.title or f"ID: {chat.id}"
        }
    else:
        try:
            chat_id = update.message.text.strip()
            if not chat_id.startswith('-100'):
                await update.message.reply_text("❌ ID de canal inválido. Deve começar com -100.")
                return SELECT_CHANNEL
            
            chat = await context.bot.get_chat(int(chat_id))
            context.user_data['channel'] = {
                'id': chat_id,
                'title': chat.title or f"ID: {chat_id}"
            }
        except Exception as e:
            await update.message.reply_text(f"❌ Erro: {e}\nPor favor, tente novamente.")
            return SELECT_CHANNEL

    # Verificar permissões do bot no canal
    try:
        chat_member = await context.bot.get_chat_member(
            int(context.user_data['channel']['id']), 
            context.bot.id
        )
        if chat_member.status not in ['administrator', 'creator']:
            await update.message.reply_text(
                "⚠️ Eu preciso ser administrador neste canal para configurá-lo.\n"
                "Por favor, me conceda permissões de administrador e tente novamente."
            )
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao verificar permissões: {e}")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("🔴 Canal de Origem", callback_data='source')],
        [InlineKeyboardButton("🟢 Canal de Destino", callback_data='destination')],
    ]
    await update.message.reply_text(
        f"📌 Canal selecionado: *{context.user_data['channel']['title']}*\n\n"
        "Este canal será de *origem* ou *destino*?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return SET_CHANNEL_TYPE

async def set_channel_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    channel_type = query.data
    context.user_data['channel_type'] = channel_type
    
    await query.edit_message_text(
        "✏️ Por favor, envie a legenda padrão para este canal.\n\n"
        "Você pode usar formatação HTML como:\n"
        "<b>negrito</b>, <i>itálico</i>, <a href='url'>link</a>\n\n"
        "Digite 'NENHUMA' para não usar legenda."
    )
    return SET_CAPTION

async def set_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caption = update.message.text
    config = load_config()
    user_config = get_user_config(update.effective_user.id, config)
    
    channel_id = context.user_data['channel']['id']
    user_config['channels'][channel_id] = {
        'title': context.user_data['channel']['title'],
        'is_source': context.user_data['channel_type'] == 'source',
        'caption': '' if caption.upper() == 'NENHUMA' else caption,
        'schedule': 'always',
        'cooldown': 0
    }
    
    save_config(config)
    
    await update.message.reply_text(
        f"✅ Configuração salva para o canal *{context.user_data['channel']['title']}*!\n\n"
        f"Tipo: {'Origem' if context.user_data['channel_type'] == 'source' else 'Destino'}\n"
        f"Legenda: {'Nenhuma' if caption.upper() == 'NENHUMA' else 'Personalizada'}\n"
        f"Agendamento: Imediato",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    user_config = get_user_config(update.effective_user.id, config)
    
    if not user_config['channels']:
        await update.message.reply_text("ℹ️ Você ainda não configurou nenhum canal.")
        return
    
    message = "📋 *Seus Canais Configurados:*\n\n"
    for channel_id, channel_data in user_config['channels'].items():
        message += (
            f"🔹 *{channel_data['title']}* (`{channel_id}`)\n"
            f"   Tipo: {'🔴 Origem' if channel_data['is_source'] else '🟢 Destino'}\n"
            f"   Legenda: {channel_data['caption'] or 'Nenhuma'}\n"
            f"   Agendamento: {channel_data['schedule']}\n\n"
        )
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.video:
        return
    
    config = load_config()
    source_chat_id = str(update.channel_post.chat.id)
    
    # Verificar configurações globais
    if not config['global_settings']['forwarding_enabled']:
        return
    
    # Verificar duração do vídeo
    if (config['global_settings']['max_duration'] > 0 and 
        update.channel_post.video.duration > config['global_settings']['max_duration']):
        logger.info(f"Vídeo ignorado - Duração excedida: {update.channel_post.video.duration}s")
        return
    
    # Processar encaminhamento para cada usuário
    for user_id, user_config in config['users'].items():
        if not user_config.get('forwarding_enabled', True):
            continue
        
        if source_chat_id in user_config['channels'] and user_config['channels'][source_chat_id]['is_source']:
            await process_forwarding(update, context, user_id, user_config, source_chat_id)

async def process_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           user_id: str, user_config: dict, source_chat_id: str):
    config = load_config()
    source_config = user_config['channels'][source_chat_id]
    
    # Verificar agendamento
    if source_config['schedule'] != 'always':
        current_day = datetime.now().strftime('%A').lower()
        if not source_config['schedule'].get(current_day, False):
            return
    
    # Verificar cooldown
    last_forward = config['users'][user_id]['channels'][source_chat_id].get('last_forward')
    if (last_forward and 
        (datetime.now() - datetime.fromisoformat(last_forward)).total_seconds() < source_config.get('cooldown', 0)):
        return
    
    # Encaminhar para canais de destino
    for channel_id, channel_config in user_config['channels'].items():
        if not channel_config['is_source'] and channel_id != source_chat_id:
            try:
                if channel_config['caption']:
                    await context.bot.send_video(
                        chat_id=int(channel_id),
                        video=update.channel_post.video.file_id,
                        caption=channel_config['caption'],
                        parse_mode='HTML'
                    )
                else:
                    await context.bot.forward_message(
                        chat_id=int(channel_id),
                        from_chat_id=int(source_chat_id),
                        message_id=update.channel_post.message_id
                    )
                
                # Atualizar último encaminhamento
                config['users'][user_id]['channels'][source_chat_id]['last_forward'] = datetime.now().isoformat()
                save_config(config)
                
                logger.info(f"Mensagem encaminhada de {source_chat_id} para {channel_id}")
                await asyncio.sleep(1)  # Pequeno delay para evitar flood
                
            except Exception as e:
                logger.error(f"Erro ao encaminhar mensagem: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operação cancelada.")
    context.user_data.clear()
    return ConversationHandler.END

# Função principal
def main():
    # Iniciar servidor Flask em thread separada
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Criar aplicação do bot
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()

    # Adicionar handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancelar", cancel))
    
    # Configurar ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("configurar", setup_channel),
            CallbackQueryHandler(setup_channel, pattern='^setup_channel$')
        ],
        states={
            SELECT_CHANNEL: [
                MessageHandler(filters.FORWARDED & filters.ChatType.CHANNEL, select_channel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, select_channel)
            ],
            SET_CHANNEL_TYPE: [
                CallbackQueryHandler(set_channel_type, pattern='^(source|destination)$')
            ],
            SET_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_caption)
            ]
        },
        fallbacks=[CommandHandler("cancelar", cancel)]
    )
    application.add_handler(conv_handler)
    
    # Outros handlers
    application.add_handler(CallbackQueryHandler(list_channels, pattern='^list_channels$'))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.VIDEO, forward_message))
    
    # Iniciar o bot
    application.run_polling()

if __name__ == '__main__':
    # Verificar se está rodando no Replit
    if 'REPL_SLUG' in os.environ:
        from keep_alive import keep_alive
        keep_alive()
    
    main()