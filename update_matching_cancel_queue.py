import re

file_path = 'bot/handlers/matching.py'

with open(file_path, 'r') as f:
    content = f.read()

# Add a simple cancel handler for the inline keyboard cancel
new_handler = """
@router.callback_query(F.data == "cancel_queue")
async def cancel_queue_inline(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("عملیات لغو شد.")
    await call.answer()
"""

if "cancel_queue_inline" not in content:
    content += "\n" + new_handler
    with open(file_path, 'w') as f:
        f.write(content)
    print("Added cancel_queue_inline to matching.py")
