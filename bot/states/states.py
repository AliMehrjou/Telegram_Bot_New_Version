from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_province = State()
    waiting_for_city = State()

class MatchingStates(StatesGroup):
    waiting_in_queue = State()
    matched_active = State()

class QuestionnaireStates(StatesGroup):
    waiting_for_questions_to_start = State()
    answering_questions = State()
    waiting_for_partner_answer = State()

class ChatStates(StatesGroup):
    waiting_for_approval = State()
    anonymous_chat_active = State()
    typing_direct_message = State()

class SupportStates(StatesGroup):
    waiting_for_support_message = State()

class ProfileEditStates(StatesGroup):
    editing_bio = State()
    selecting_interests = State()

class DiscoveryStates(StatesGroup):
    pass

class ReportStates(StatesGroup):
    waiting_for_report_reason = State()

class AdminStates(StatesGroup):
    pass
