"""Tool dispatch receipts for an active local job, without storing tool payloads.

Returned means the tool returned a receipt, not verified task completion.
Unknown tools are conservatively treated as possibly changing external state.
"""


READ_ONLY = {
    'desktop_observe', 'desktop_read', 'system_overview', 'local_ai_status',
    'browser_connection_status', 'calendar_events', 'recent_tool_activity',
    'find_files', 'list_folder', 'read_file', 'view_image_file', 'read_word_document', 'read_document',
    'list_documents', 'search_notes', 'list_reminders', 'list_startup_apps',
    'list_windows', 'inspect_system', 'list_saved_logins', 'list_tabs', 'read_tab',
    'search_email', 'read_email', 'search_internet', 'read_web_page',
    'search_public_web', 'read_public_page', 'read_video_transcript',
    'list_browser_tasks', 'inspect_browser_task', 'inspect_browser_form',
    'search_saved_facts',
    'reliability_status','scorpred_status','scorpred_credits','scorpred_pick_record','scorpred_insights','scorpred_props','scorpred_player_prop','paper_connection_status',
    'forex_setups',
}


class WorkLedger:
    def __init__(self, store, current_job, output_roots=()):
        self.store, self.current_job = store, current_job
        self.output_roots = tuple(output_roots)

    def capture(self, receipt, name, arguments, result):
        from .goal_verification import capture_output
        capture_output(self.store, receipt, name, arguments, result, self.output_roots)

    def before(self, name):
        job = self.current_job()
        if not job:
            return None
        side_effect = name not in READ_ONLY
        step = self.store.start_step(job, name, side_effect=side_effect)
        return step, side_effect

    def after(self, receipt, is_error):
        if receipt is None:
            return
        step, side_effect = receipt
        self.store.finish_step(step, 'uncertain' if is_error and side_effect else 'failed' if is_error else 'returned')
