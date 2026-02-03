import asyncio
from playwright.async_api import async_playwright

async def inspect():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        print("Navigating to https://tophub.today/daily ...")
        await page.goto("https://tophub.today/daily")
        await page.wait_for_timeout(2000)
        
        # Try to find news items
        # Based on common structures, looking for lists or articles
        # Let's grab the text content of the main area or specific classes if we can guess
        
        # Get all text to see what we have
        content = await page.content()
        
        # Extract some potential titles using selectors
        # Common selectors for TopHub might be .item-title, .title, etc.
        # Let's try to get elements that look like news items
        
        # Strategy: Get all links inside the main content area
        links = await page.evaluate("""() => {
            const anchors = Array.from(document.querySelectorAll('a'));
            return anchors.map(a => ({text: a.innerText, href: a.href}))
                          .filter(a => a.text.length > 10);
        }""")
        
        print(f"Found {len(links)} potential links.")
        for i, link in enumerate(links[:20]):
            print(f"{i}: {link['text']} - {link['href']}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(inspect())
