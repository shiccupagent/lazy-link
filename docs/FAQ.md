# Lazy Link: questions and answers

Short answers to what people ask most. The safety rules are in the README under
[Safety, in plain words](../README.md#safety-in-plain-words).

## Can a job delete or change my files on the PC?

Jobs run as `lazylink`, a standard account. It can write only in its own folders: the work folder `C:\lazylink` and its own profile.
Model folders you list in `extra_model_folders` are locked read-only for it: it can use the models,
but it cannot change, delete or re-permission them. Your own account is not affected by the lock.

## Why is the lock a DENY, not just "no permission"?

Windows adds up permissions from every group the account is in, so a plain "no permission" can be
undone by a broader group. A DENY entry always wins. Setup-PC adds it with `icacls ... /deny` for
`lazylink` only, on write, delete, delete-child, and change-permission and change-owner rights.

## Why do I run Setup-PC twice?

Setup-PC reads your model folders from ComfyUI's list, and that list is written when the Mac runs
`./imagegen/imagegen.py setup`. So run Setup-PC first (it needs admin, and it is the only step that does),
then run `imagegen.py setup` on the Mac, then run Setup-PC once more so it can lock the folders.
Running it again is safe: it re-applies the same locks and removes nothing.

## A check says a model folder is "not locked read-only yet"

The folder is listed in `config.json`, but the lock is not on it yet. Double-click `pc\Setup-PC.cmd` on the PC.
Nothing else changes.

## A check says the lazylink account "can't read" a model folder

Jobs can't reach that folder, so its models can't be used. `./check.py` prints a one-line command for an admin
PowerShell on the PC that gives the account read access (read only, nothing else). Paste it there.

## Can I lock a folder that is not in config.json?

Yes. Run Setup-PC from an admin PowerShell with `-ExtraModelFolder "D:\Models\Something"`. The folder is then
locked along with the others. Setup-PC never locks a drive root or a folder inside `C:\lazylink`.

## How do I take it all back?

Double-click `pc\Undo-PC.cmd`. It removes the locks it added, the `lazylink` account, the firewall rule and the
job runner. Your model folders are left as they were. Add `-RemoveWorkFolder -RemoveOpenSSH` to remove the work folder
and OpenSSH Server too.

## Can a job read my other files?

A job is a PowerShell script that runs as `lazylink`, so it can read whatever a standard Windows account can read.
It cannot write outside `C:\lazylink`, its own profile and the locked model folders. Your own profile folders
(Documents, Desktop) are private to your account by default. Keep anything sensitive in a folder only you can open.

## Does the PC send my files or prompts to the internet?

No. Your prompts and files stay on your own machines. The only internet traffic is downloading models from their
official sites and reading public model lists (`imagegen/watch.py`).

## Does `./check.py` change anything on the PC?

The report only reads the PC: graphics card, memory, disks, updates, wake settings, and the extra model folders (read access
and the lock). Two flags change things. `./check.py --setup` installs what the report recommends, so it downloads models.
`./check.py --tidy` deletes Lazy Link's own old files, and it asks you first.
