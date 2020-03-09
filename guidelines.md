This document outlines the moderation commands of the DCTV bot. It also has best practice recommendations.  

**Bot Name:** `DCTV#5882`
**Bot Prefix:** `-`

## Commands

**Note:** Parameters inside `<>` are required. Those inside `[]` are optional and those in `{}` are optional but __recommended__. Parameter names followed by `=value` indicate that the default value of the parameter is `value`.  

Don’t use the commands in a public channel. Use the `#mod-bot-commands` channel for these commands. The exception to this are emergency cases (like raids) and the commands which affect the channel they are used in, like `slowmode`. Please remember to delete the command and the bot reply later on.  

`mute <user> [duration] {reason}`: Adds the `Muted` role to the specified user and sends a message about (__with the optional duration and reason__) it in the `#muted` channel, tagging the user. Users with the `Muted` role cannot chat in any channel.

`unmute <user> [reason]`: Removes the `Muted` role from the specified user.

`cleanup <clean_type> <extra>`: Deletes messages! More info in the `Parameters` section.

`note <user> {note}`: Adds a note to the specified user. Useful when you need to warn a user (more on warnings below).

`ban <user> [days=0] {reason}`: Bans the specified user and deletes `days` days worth of their message history.

`softban <user> {reason}`: Immediately ban and unban the specified user and delete 1 day worth of their message history. Effectively works like a `kick` + `cleanup`.

`unban <user_id> {reason}`: Unban the user with the specified user ID.

`tempban <user> [days=1] {reason}`: Temporarily ban the specified user for `days` days.

`kick <user> {reason}`: Kick the specified user from the server.

`hackban <user_ids> [days=1] {reason}`: Bans users from the server even if they aren't on the server anymore, deleting `days` days worth of their message history (if any). Multiple user ids can be given at once. They are separated by `spaces`.

`role add <user> <role>`: Removes the specified role from the specified user. Don't use this to mute a user.

`role remove <user> <role>`: Removes the specified role from the specified user. Don't use this to unmute a user.

`inrole <role>`: Lists all the users with the specified role.

`userinfo [user]`: Displays information about the specified user. Leaving user blank returns your own info.

`search <name> [flag]`: Searches for a user using a part of their name. Optional parameter `flag` can be used to refine the search. More on flags under the `Parameters` section below.

`cases <user>`: Lists moderation actions taken against the specified user. To get more information about a case, use `case <case_number>`.

`server`: Displays server statistics.

`slowmode [duration]`: Sets a slowmode in the channel the command is used. `duration`  should be between 5 seconds and 6 hours. Leaving `duration` blank removes the slowmode.

`uslowmode [channel] [duration]`: Sets a custom slowmode over 6 hours in the channel. Use `slowmode` for slowmodes under 6 hours. `channel` defaults to the channel the command is used in. Leaving `duration` blank removes the slowmode.

`role info <role>`: Shows info about the specified role.

`nickname <user> [name]`: Sets a new nickname for the specified user. Leaving the name blank will remove their current nickname, if any.

`bans`: Lists active server bans.

## Parameters

### User

`user` can be user ID or name (case and spaces sensitive) with or without discriminator. It's generally a good practice to include the discriminator or use the user ID. `search` command can be useful for getting the ID or the full username.

### Duration

`duration` should be entered in the following format: `1d5h4m10s`. You can add spaces in between but it's preferable to not add them. The duration doesn't have to be specific. `1d5s`, `5s`, `10h4s`, etc., are all valid.

### Role

`role` must be the role ID or name (case and spaces sensitive). `role info` command can be useful for getting the ID or the full name.

### Flag

`flag` can be one of the following:

- `-cs` Performs case-sensitive search. Flag alias: `-case-sensitive`
- `-f` Returns the result in a file instead of a message. Useful for searches for a common name. Flag alias: `-file`
- `-b` Only includes bot users in the search results. Flag alias: `-bot`

You can combine any two flags by typing them together. For example, to perform a case-sensitive search and return the result in a file, the flag would be `-csf` or `-fcs`. Note that this doesn't work with the aliases. So `-case-sensitivefile` will not work.

### Channel

`channel` can be channel ID or name (case and spaces sensitive).

### Cleanup

`extra` depends on the `clean_type` and can be more than just one parameter. The combination of `clean_up` and `extra` may be one of the following:

- `messages <number> [delete_pinned=False]` Delete last `number` messages in the channel the command is used.
- `user <user> <number> [delete_pinned=False]` Delete last `number` messages sent by `user` in the channel the command is used.
- `after <message_id> [delete_pinned=False]` Delete all messages sent in the channel the command is used after the message with message ID equal to `message_id`.
- `before <message_id> <number> [delete_pinned=False]` Delete `number` messages sent in the channel the command is used before the message with message id equal to `message_id`.
- `between <one> <two> [delete_pinned=False]` Delete all messages in the channel the command is used between messages with IDs `one` and `two`.
- `bot <number> [delete_pinned=False]` Delete last `number` messages sent by bots in the channel the command is used.
- `self <number> [match_pattern] [delete_pinned=False]` Delete last `number` messages sent by the bot itself in the channel the command is used. `match_pattern` can be used to specify which messages to delete. It can be simple subtext or regex (the regex should start with `r(` and end with `)`). `match_pattern` must be enclosed in double quotes ("").
- `text <text> <number> [delete_pinned=False]` Delete last `number` messages which contain the `text` substring in the channel the command is used.

`delete_pinned` can be set to True to delete pinned messages as well.

Example command: `[p]cleanup self 10 "r(.*\d{2} days)" True`. Running this command deletes the last `10` messages, including pinned, sent by the bot which contain a two digit number followed by " days".

### Misc

`note`: text, supports markdown
`reason`: text, supports markdown
`name`: text, doesn't support markdown
`case_number`: a number

## Warnings

Warnings can be given in the following ways:

- in the channel itself (for recent, non-severe violations)
- via DM (for severe violations)
- via ModMail (for severe violations) (more about ModMail in the `#modmail-info` channel)  

**For the last two types of warnings, make sure to add a `note` using the `note` command.**

## Appeals

For mute, kick or ban appeals, please redirect users to the ModMail.
