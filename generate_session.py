"""
Bu skript sizning kompyuteringizda BIR MARTA ishga tushiriladi.
Maqsad: mavjud session_36743273.session faylini o'qib, uni matn
(StringSession) ko'rinishiga o'girib beradi. Shu matnni Render'dagi
TELEGRAM_SESSION_STRING muhit o'zgaruvchisiga qo'yasiz — shunda Render
serverida qayta SMS-kod so'ralmaydi (chunki server konsolida kod
kiritish imkoni yo'q).

Ishlatish:
1. Ushbu faylni session_36743273.session bilan BIR papkaga joylang.
2. Terminalda: pip install telethon
3. python generate_session.py
4. Chiqqan uzun matnni to'liq nusxalab, Render Dashboard ->
   sizning servisingiz -> Environment -> TELEGRAM_SESSION_STRING
   qatoriga joylashtiring.
"""

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 36743273
API_HASH = "f6a0f9a4f1bbfdd95b17061b65d1d97c"

# Diqqat: bu skript session_36743273.session faylidan foydalanadi,
# ya'ni siz avval shu fayl orqali (masalan get_channel_id.py yordamida)
# kirish qilgan bo'lishingiz kerak.
with TelegramClient("session_36743273", API_ID, API_HASH) as client:
    print("\n=== TELEGRAM_SESSION_STRING ===\n")
    print(client.session.save())
    print("\n================================\n")
    print("Yuqoridagi matnni Render'dagi TELEGRAM_SESSION_STRING ga qo'ying.")
