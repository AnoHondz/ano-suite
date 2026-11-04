"""Account-free capability facts derived from the tools advertised this turn."""


def integration_context(definitions):
    names = {item.get('name') for item in definitions}
    mail = {'search_email', 'read_email', 'send_email'} & names
    outlook = ('\nOutlook on this PC is available: outlook_inbox, outlook_read_email, outlook_calendar, and '
               'outlook_draft_reply, which opens a filled-in reply for the user to send (listed by find_tool if not shown). '
               'Use them for "my email/inbox/calendar" unless Gmail is named. Nothing can be sent, moved or deleted '
               'by Ano.') if {'outlook_inbox'} & names else ''
    if not mail:
        return outlook + ('\nRuntime integration status: Gmail API/IMAP integration is not connected in this Ano session. '
                'If asked to search, read or send Gmail, say plainly that Gmail is not connected. '
                'Do not invent inbox contents or claim an email was sent. An authorized browser workflow '
                'may still be available; inspect its actual sign-in and result before claiming access. '
                'Never request an account password or app password in chat.')
    return outlook + ('\nRuntime integration status: Gmail tools available: ' + ', '.join(sorted(mail)) + '. '
            'Tool availability does not prove the credentials are valid, the network works, or the inbox '
            'was read. Use the actual tool result and report authentication or connection failures plainly.')
