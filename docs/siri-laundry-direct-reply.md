# Direct iPhone laundry reply

On November 4, the iPhone invoked Check the laundry and received HTTP 500.
M4 logged the diagnostic request at 12:37:10 Pacific. The diagnostic text was
successfully generated, but the HomePod email relay failed, causing the HTTP
endpoint to fail the entire phone command.

The restricted SSH dispatcher now handles the allowlisted `diagnostics`
command by running the local briefing CLI without `--speak` and returning
its text directly. An iPhone reply no longer depends on Mail, email delivery,
or Intercom. Read failures and empty replies remain errors. Other command
allowlists and SSH key restrictions remain unchanged.

Five reply tests pass, including a simulated unavailable HTTP relay and a
failed local read. The deployed forced-command script returned diagnostic text.
A separate live relay retry succeeded after restarting Mail. iPhone Siri speech
requires a post-deployment phone test and is not claimed by these server checks.
The Mac's local shortcut test encountered an acknowledgement-only SSH key;
that restriction was retained rather than broadening the key's access.
