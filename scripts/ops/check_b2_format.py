"""
Backblaze B2 S3-Compatible API Key Format:

When you view your Application Key in the B2 console, you should see:
- keyID: Should be 25 characters (example: 004c6b021b5a16000000000001)
- applicationKey: 40+ characters (your: 00375a2d3225788e08472bed0aec292c1e166269e1 ✓)

The keyID you provided (4c6b021b5a16) is only 12 characters - it's incomplete.

Please check your B2 console again:
1. Go to: Account → App Keys
2. Find your "Master Application Key"
3. Click "Show" or look at the full keyID column
4. The full keyID should start with "004" or "005" and be 25 chars total

Example format: 004c6b021b5a16000000000001
Your partial ID: 4c6b021b5a16 (missing ~13 characters)
"""
print(__doc__)
