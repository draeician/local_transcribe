# Install browser_cookie3 if needed
#pip install browser_cookie3

# Extract Firefox cookies
import browser_cookie3
import http.cookiejar

# Get Firefox cookies
cj = browser_cookie3.firefox(domain_name='youtube.com')

# Save to cookies.txt format
with open('cookies.txt', 'w') as f:
    f.write('# Netscape HTTP Cookie File\n')
    for cookie in cj:
        f.write(f"{cookie.domain}\tTRUE\t{cookie.path}\t{'TRUE' if cookie.secure else 'FALSE'}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n")

print("Cookies saved to cookies.txt")

# Then use it
#lt batch --resume --cookies-file cookies.txt
