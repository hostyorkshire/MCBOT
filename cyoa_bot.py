async def handle_message(event) -> None:  # type: ignore[type-arg]
    """Process an incoming direct message event."""
    nonlocal _bot_error_count
    payload = event.payload
    pubkey_prefix: str = payload.get("pubkey_prefix", "")
    text: str = payload.get("text", "").strip()

    if not pubkey_prefix or not text:
        return

    # Look up a friendly name for the sender.
    contact = mc.get_contact_by_key_prefix(pubkey_prefix)
    
    # If contact doesn't exist, try to add them automatically
    if not contact:
        try:
            await mc.commands.add_contact(pubkey_prefix)
            log.info("Auto-added new contact: %s", pubkey_prefix)
            # Re-fetch contacts to populate the cache
            await mc.commands.get_contacts()
            # Try the lookup again
            contact = mc.get_contact_by_key_prefix(pubkey_prefix)
        except Exception as exc:
            log.warning("Failed to auto-add contact %s: %s", pubkey_prefix, exc)
    
    user_name: str = (
        contact.get("adv_name", "Adventurer").strip() or "Adventurer"
        if contact
        else "Adventurer"
    )

    snippet = text[:80] + ("…" if len(text) > 80 else "")
    log.info("Message from %s (%s): %r", user_name, pubkey_prefix, snippet)

    try:
        await handler.handle(pubkey_prefix, text, user_name)
    except Exception:
        _bot_error_count += 1
        raise
