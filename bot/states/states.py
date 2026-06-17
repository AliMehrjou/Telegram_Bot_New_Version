from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_province = State()  # جدید
    waiting_for_city = State()

class MatchingStates(StatesGroup):
    waiting_in_queue = State()
    matched_active = State()

class QuestionnaireStates(StatesGroup):
    waiting_for_questions_to_start = State()  # جدید (برای وقفه ۵ ثانیه)
    answering_questions = State()
    waiting_for_partner_answer = State()

class ChatStates(StatesGroup):
    waiting_for_approval = State()
    anonymous_chat_active = State()
    typing_direct_message = State()  # جدید (برای پیام ناشناس)

class SupportStates(StatesGroup):
    waiting_for_support_message = State()  # جدید

class ProfileEditStates(StatesGroup):
    editing_bio = State()
    selecting_interests = State()