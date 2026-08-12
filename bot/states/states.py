from aiogram.fsm.state import State, StatesGroup

class OnboardingStates(StatesGroup):
    waiting_for_terms_acceptance = State()
    waiting_for_name     = State()
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

class DirectMessageStates(StatesGroup):
    typing_message         = State()
    viewing_inbox          = State()
    viewing_message        = State()
    replying               = State()

class SupportStates(StatesGroup):
    waiting_for_support_message = State()  

class AdminStates(StatesGroup):
    waiting_for_support_reply       = State()
    waiting_for_broadcast_message   = State()
    waiting_for_card_number         = State()
    waiting_for_card_holder         = State()
    confirming_card_change          = State()

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
    choosing_distance  = State()
    choosing_search_type = State()

class ReportStates(StatesGroup):
    selecting_reason = State()
    waiting_for_report_description = State() 
    waiting_for_evidence_before_reason = State() 

class VIPStates(StatesGroup):
    waiting_for_age_filter = State()
    choosing_plan = State()
    choosing_payment_method = State()
    waiting_for_receipt = State()

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

class PaymentStates(StatesGroup):
    choosing_package = State()
    choosing_method = State()
    waiting_for_receipt_photo = State()

class QuestionAddStates(StatesGroup):
    choosing_type     = State()   
    entering_text     = State()   
    entering_option_a = State()
    entering_option_b = State()
    entering_option_c = State()  
    entering_option_d = State()   
    entering_category = State()   
    confirming        = State()   
    waiting_for_excel = State()   
    confirming_bulk   = State()   

class ProfileCommentStates(StatesGroup):
    waiting_for_comment_text = State()

class GiftStates(StatesGroup):
    choosing_gift          = State()  
    choosing_quantity      = State()  
    confirming_purchase    = State()
    waiting_for_recipient  = State()  
    confirming_transfer    = State()
    # استیت‌های جدید برای فاز فروش
    choosing_sell_gift     = State()
    choosing_sell_quantity = State()


class HelpStates(StatesGroup):
    viewing_main_menu      = State()
    viewing_sub_topic      = State()

class ReferralStates(StatesGroup):
    viewing_dashboard      = State()

class CoinsMenuStates(StatesGroup):
    viewing_history        = State()
    choosing_package       = State()
    choosing_payment       = State()

class ProfileCompletionStates(StatesGroup):
    setting_photo          = State()
    setting_gps            = State()
    setting_tags           = State()
    setting_bio            = State()
    setting_city           = State()
    setting_voice          = State()
    completed              = State()

class TagSelectionStates(StatesGroup):
    selecting_tags         = State()

class BannerForwardStates(StatesGroup):
    forwarding_banner      = State()

class WarningReviewStates(StatesGroup):
    reviewing_report       = State()
    reviewing_banner       = State()

class GiftAddStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_name = State()
    waiting_for_emoji = State()
    waiting_for_price = State()
    waiting_for_description = State()
    confirming = State()

class AnonymousLinkStates(StatesGroup):
    waiting_for_message = State()

class BannerAddStates(StatesGroup):
    waiting_for_photo = State()
    waiting_for_caption = State()
    waiting_for_reward = State()
    confirming = State()

class ManualTransferStates(StatesGroup):
    waiting_for_target_id = State()