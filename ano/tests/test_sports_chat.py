from unittest import TestCase
from ano.sports_chat import compact_insights,parse_player_prop,player_prop_reply
from ano.sports_reports import SportsReports
from unittest.mock import Mock
from pathlib import Path


class ChatTests(TestCase):
    def test_nba_email_uses_same_both_side_brief_without_diagnostic_dump(self):
        report=SportsReports(Path('unused'),getter=Mock(return_value=({'result':{
            'choices':{'over':[{'player':'Over Player','team':'Toronto Raptors','line':10.5,'label':'Points','probability':.6}],
                       'under':[{'player':'Under Player','line':15.5,'label':'Points','probability':.65}]},
            'notice':'Saved unvalidated reference lines; prices unverified.'}},200)))
        report.identity=Mock()
        report.snapshot=Mock(return_value=({'slate':[{'teamA':'A','teamB':'B','side':'A','winProbability':60,
            'reason':'RAW_DIAGNOSTIC '*100}]},200))
        result=report.build('nba',props=True,day='2026-10-30')
        self.assertIn('OVERS',result['content']);self.assertIn('UNDERS',result['content'])
        self.assertIn('Over Player (Toronto Raptors) OVER',result['content'])
        self.assertIn('<b>Under Player UNDER',result['content'])  # an older snapshot without a team still renders
        self.assertNotIn('RAW_DIAGNOSTIC',result['content']);self.assertEqual(result['count'],2)

    def test_props_show_live_sportsbook_lines_beside_the_reference_line(self):
        from datetime import datetime,timedelta,timezone
        soon=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
        gone=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        quote=lambda book,line,odds,implied,expires=soon,side='under':{'bookmaker':book,'line':line,'side':side,'decimal_odds':odds,
            'implied_probability':implied,'model_chance':.5579,'expires_at':expires,'fetched_at':'2026-11-01T20:30:45+00:00'}
        pick={'name':'Tyler Higbee','team':'LA','side':'under','line':4.5,'label':'Receptions','projection':3.16,'chance':.6415,'edge':.18,
              'line_source':'historical','sportsbook_quotes':[quote('DraftKings',3.5,1.81,.5525),quote('FanDuel',3.5,1.83,.5464),
                  quote('Stale',3.5,1.9,.5,expires=gone),quote('Other side',3.5,1.97,.5,side='over')]}
        getter=Mock(return_value=({'result':{'roster_verification':{'status':'verified'},'picks':[pick]}},200))
        report=SportsReports(Path('unused'),getter=getter);report.identity=Mock()
        report.snapshot=Mock(return_value=({'slate':[{'matchId':'g','pregameAvailable':True,'matchup':'Rams vs Chargers'}]},200))
        html=report.build('nfl',props=True,day='2026-11-01')['content']
        self.assertIn('Sportsbook: Under 3.5</b> — DraftKings 1.81, FanDuel 1.83',html)
        self.assertIn('model 55.8% at this line',html);self.assertIn('book implied 54.6%–55.2%',html)
        self.assertNotIn('Stale',html);self.assertNotIn('Other side',html)
        self.assertIn('real book lines below',html);self.assertNotIn('unknown games',html)
        # Estimates that exist but fall short are reported as such, not as missing evidence.
        getter.return_value=({'result':{'roster_verification':{'status':'verified'},'picks':[{**pick,'chance':.5975},{**pick,'chance':.55}]}},200)
        html=report.build('nfl',props=True,day='2026-11-01')['content']
        self.assertIn('2 prop estimates were checked; none reached the 60% model threshold (best 59.8%)',html)

    def test_default_is_short_and_omits_raw_diagnostic_dump(self):
        rows=[{'teamA':'A','teamB':'B','side':'A','winProbability':60,'action':'SKIP','researchPick':True,
               'reason':'PROVISIONAL: source observed expires rule confidence '*100} for _ in range(9)]
        text=compact_insights('nba','2026-10-30',{'slate':rows},200)
        self.assertLess(len(text),850);self.assertNotIn('rule confidence',text)
        self.assertEqual(text.count('A vs B'),3);self.assertIn('early read',text)
        self.assertIn('NBA for Fri, Oct 30',text);self.assertNotIn('model',text)

    def test_named_combination_and_common_spacing_typo_route_without_ai(self):
        q=parse_player_prop('check Naz Reid over20pts and assits')
        self.assertEqual(q,{'player':'naz reid','side':'over','line':20.,'market':'pa'})
        self.assertIsNone(parse_player_prop('email Naz Reid over 20 points and assists'))

    def test_whole_line_probability_includes_push_and_does_not_claim_price(self):
        text=player_prop_reply({'player':'Naz Reid','side':'over','line':20,'label':'Points + assists',
            'projection':21,'minutes':27,'status':'WAIT','reason':'exact price unavailable',
            'forecast':{'over_probability':.5,'under_probability':.4,'push_probability':.1,
              'historical':{'games':4,'over_hits':2,'under_hits':1}}})
        self.assertIn('about a 50% chance (40% for the under)',text)
        self.assertIn("about a 10% chance he lands exactly on 20, a push",text)
        self.assertIn('gone over in 2 of his last 4 games',text);self.assertIn('My call: hold off for now (exact price unavailable).',text)
        self.assertIn("can't see the sportsbook's price",text)
        for jargon in ('Unvalidated','WAIT','appearances','unverified'):self.assertNotIn(jargon,text)
