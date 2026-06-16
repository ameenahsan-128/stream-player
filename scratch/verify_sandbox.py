import re

def verify():
    with open("generate_player.py", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Find the iframe-player definition
    match = re.search(r'<iframe id="iframe-player".*?>', content, re.DOTALL)
    if not match:
        print("[-] Error: <iframe id=\"iframe-player\"> tag not found in generate_player.py")
        return False
    
    tag = match.group(0)
    print(f"[+] Found iframe tag: {tag}")
    
    sandbox_match = re.search(r'sandbox=["\'](.*?)["\']', tag)
    if not sandbox_match:
        print("[-] Error: sandbox attribute not found in the iframe tag")
        return False
    
    sandbox_val = sandbox_match.group(1)
    print(f"[+] Sandbox value: {sandbox_val}")
    
    passed = True
    if "allow-popups" in sandbox_val:
        print("[+] Success: allow-popups is present in the sandbox")
    else:
        print("[-] Failed: allow-popups is NOT present in the sandbox")
        passed = False
        
    if "allow-top-navigation" in sandbox_val:
        print("[-] Failed: allow-top-navigation is present in the sandbox")
        passed = False
    else:
        print("[+] Success: allow-top-navigation is NOT present in the sandbox")
        
    return passed

if __name__ == "__main__":
    if verify():
        print("[+] All verification checks PASSED successfully.")
    else:
        print("[-] Verification checks FAILED.")
