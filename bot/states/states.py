from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_terms_acceptance = State()
    waiting_for_gender   = State()
    waiting_for_age      = State()
    waiting_for_province = State()
    waiting_for_city     = State()

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

class AdminStates(StatesGroup):
    waiting_for_support_reply       = State()
    waiting_for_broadcast_message   = State()


class ProfileEditStates(StatesGroup):
    editing_bio = State()
    selecting_interests = State()
    waiting_for_photo = State()        
    editing_name = State()   
    updating_age = State()            
    updating_province = State()        
    updating_city = State()
    waiting_for_voice = State()
    waiting_for_gps = State()          

class DiscoveryStates(StatesGroup):
    choosing_province  = State()   
    choosing_interests = State()   
    choosing_age_range = State()   
    showing_results    = State()   
    navigating         = State()

class ReportStates(StatesGroup):
    selecting_reason = State()
    waiting_for_report_description = State() 
    waiting_for_evidence_before_reason = State() 

class VIPStates(StatesGroup):
    waiting_for_age_filter = State()

class EventStates(StatesGroup):
    waiting_for_name        = State()
    waiting_for_description = State()
    waiting_for_duration    = State()
    waiting_for_multiplier  = State()
    confirming              = State()

 
class PBroadcastStates(StatesGroup):
    waiting_for_filter  = State()
    waiting_for_message = State()
    confirming          = State()

class CoinTransferStates(StatesGroup):
    waiting_for_amount = State()
    confirming         = State()

class TransferCoinStates(StatesGroup):
    waiting_for_amount = State()

# ================== کدهای افزودنی ==================
class PaymentStates(StatesGroup):
    choosing_package = State()
    choosing_method = State()
    waiting_for_receipt_photo = State()