import frappe

def test_source():
    from crm.www.api import _process_whatsapp_message
    
    # 1. Test Website
    msg1 = {
        "from": "919999999991",
        "type": "text",
        "text": {"body": "Hi, I'm interested through your website"},
        "id": "wamid.TEST1",
        "timestamp": "1710000000"
    }
    contacts1 = [{"wa_id": "919999999991", "profile": {"name": "Web User"}}]
    _process_whatsapp_message(msg1, contacts1)
    print("Test 1 Complete")
    
    # 2. Test Instagram
    msg2 = {
        "from": "919999999992",
        "type": "text",
        "text": {"body": "Hi, I found you on Instagram"},
        "id": "wamid.TEST2",
        "timestamp": "1710000000"
    }
    contacts2 = [{"wa_id": "919999999992", "profile": {"name": "Insta User"}}]
    _process_whatsapp_message(msg2, contacts2)
    print("Test 2 Complete")

    # 3. Test Meta Ad
    msg3 = {
        "from": "919999999993",
        "type": "text",
        "text": {"body": "Hello Ad!"},
        "id": "wamid.TEST3",
        "timestamp": "1710000000",
        "referral": {
            "headline": "Summer Smart Home Sale",
            "ctwa_clid": "click_abc123"
        }
    }
    contacts3 = [{"wa_id": "919999999993", "profile": {"name": "Ad User"}}]
    _process_whatsapp_message(msg3, contacts3)
    print("Test 3 Complete")
    
    frappe.db.commit()

