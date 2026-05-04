# -*- coding: utf-8 -*-
"""
Elite League Configuration
"""
import os

# League ID for your H2H league
LEAGUE_ID = 639056

# Authentication cookies - READ FROM ENVIRONMENT VARIABLES (secure)
COOKIES = {
    'sessionid': os.environ.get('FPL_SESSION_ID', ''),
    'csrftoken': os.environ.get('FPL_CSRF_TOKEN', '')
}

# Postponed games (update as needed)
POSTPONED_GAMES = {}

# Players to exclude from standings (if any)
EXCLUDED_PLAYERS = ["Mustafa Elessawi"]

# First GW of the FPL-managed knockout phase. From this GW onward FPL sends
# only the matches between still-active managers, and the round-robin league
# total stops accumulating. Knockout match results (W/L) are still displayed
# but no longer add +3/+1/+0 to projected_league_points, so the standings
# table stays frozen at the league-phase final order — matching FPL's UI.
KNOCKOUT_START_GW = 36

# API Base URLs
FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Arabic Translations
ARABIC = {
    # Main titles
    'app_title': 'دوري النخبة',
    'elite_league': 'ELITE LEAGUE',
    'fpl_tracker': 'فانتازي الدوري الإنجليزي',
    
    # Navigation & Status
    'live': 'مباشر',
    'gameweek': 'الجولة',
    'current_gameweek': 'الجولة الحالية',
    'not_started': 'لم تبدأ',
    'finished': 'انتهت',
    'in_progress': 'جارية',
    
    # Fixtures Table
    'fixtures': 'جدول المباريات',
    'h2h_matches': 'جدول المباريات',
    'last_gw_matches': 'مباريات الجولة السابقة',
    'team_1': 'المدرب الأول',
    'team_2': 'المدرب الثاني',
    'score': 'النتيجة',
    'points_diff': 'الفارق',
    'vs': 'ضد',
    'winner': 'فائز',
    'loser': 'خاسر',
    'draw': 'تعادل',
    
    # Standings Table
    'standings': 'ترتيب الدوري',
    'live_standings': 'الترتيب المباشر',
    'final_standings': 'ترتيب الدوري',
    'rank': 'م',
    'change': 'التغيير',
    'manager': 'المدرب',
    'team_name': 'اسم الفريق',
    'league_points': 'نقاط الدوري',
    'gw_points': 'نقاط الجولة',
    'total_points': 'مجموع النقاط',
    'overall_rank': 'الترتيب العام',
    'captain': 'الكابتن',
    'chip': 'الخاصية',
    'result': 'النتيجة',
    'opponent': 'الخصم',
    
    # Chips
    'wildcard': 'وايلد كارد',
    'freehit': 'فري هيت',
    'bench_boost': 'بنش بوست',
    'triple_captain': 'تريبل كابتن',
    'assistant_manager': 'المدير المساعد',
    'no_chip': 'بدون خاصية',
    
    # Status messages
    'gw_not_started': 'الجولة لم تبدأ بعد',
    'loading': 'جاري التحميل...',
    'error': 'خطأ',
    'auto_refresh': 'تحديث تلقائي كل 60 ثانية',
    'last_updated': 'آخر تحديث',
    
    # Footer
    'copyright': '© جميع الحقوق محفوظة لـ ربيع الشتيوي',
    
    # Stats Page
    'stats': 'الإحصائيات',
    'stats_page': 'إحصائيات الدوري',
    'captaincy_stats': 'إحصائيات الكابتن',
    'captain_name': 'اسم اللاعب',
    'captain_count': 'عدد مرات الاختيار',
    'chips_stats': 'الخصائص المستخدمة',
    'chip_name': 'الخاصية',
    'used_by': 'المدرب',
    'no_chips': 'لا توجد خصائص مستخدمة',
    'points_stats': 'إحصائيات النقاط',
    'minimum': 'خازوق الجولة',
    'maximum': 'ملك الجولة',
    'average': 'المتوسط',
    'best_rank': 'أفضل ترتيب',
    'worst_rank': 'أسوأ ترتيب',
    'lucky': 'محظوظ الجولة',
    'unlucky': 'منحوس الجولة',
    'median': 'الوسيط',
    'lower_quartile': 'الربع الأدنى',
    'upper_quartile': 'الربع الأعلى',
    'effective_ownership': 'نسبة الملكية الفعالة',
    'player_name': 'اللاعب',
    'team': 'الفريق',
    'eo_count': 'العدد',
    'eo_percentage': 'النسبة',
    'back_to_home': 'العودة للرئيسية',
    'unique_players': 'اللاعبين المميزين',
    'show_unique': 'عرض اللاعبين المميزين',
    'hide_unique': 'إخفاء',
    
    # Comparison section
    'comparison': 'مقارنة المدربين',
    'comparison_type': 'نوع المقارنة',
    'points_comparison': 'مقارنة النقاط',
    'rank_comparison': 'مقارنة الترتيب',
    'select_manager_1': 'اختر المدرب الأول',
    'select_manager_2': 'اختر المدرب الثاني',
    'loading_data': 'جاري تحميل البيانات...',
    'refresh': 'تحديث',
    'gw_label': 'الجولة',
    'points_label': 'النقاط',
    'rank_label': 'الترتيب',
}

def get_chip_arabic(chip):
    """Get Arabic name for chip"""
    chip_map = {
        'wildcard': ARABIC['wildcard'],
        'freehit': ARABIC['freehit'],
        'bboost': ARABIC['bench_boost'],
        '3xc': ARABIC['triple_captain'],
        'manager': ARABIC['assistant_manager'],
    }
    return chip_map.get(chip, ARABIC['no_chip']) if chip else ARABIC['no_chip']


def is_chip_active(chip):
    """Check if a chip is active (not 'no chip')"""
    return chip is not None and chip != ''
