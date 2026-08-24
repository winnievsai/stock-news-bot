-- 透過 Mail.app 寄信，用命令列參數傳入內容，避免特殊字元跳脫問題
-- 用法：osascript send_mail.applescript <寄件人Email> <收件人Email> <主旨> <內文>
on run argv
    set theSender to item 1 of argv
    set theRecipient to item 2 of argv
    set theSubject to item 3 of argv
    set theBody to item 4 of argv

    tell application "Mail"
        set newMsg to make new outgoing message with properties {subject:theSubject, content:theBody, visible:false}
        tell newMsg
            set sender to theSender
            make new to recipient at end of to recipients with properties {address:theRecipient}
        end tell
        send newMsg
    end tell
end run
