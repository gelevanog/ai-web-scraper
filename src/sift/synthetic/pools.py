"""Word pools for the synthetic web. Every name, company, street and person here is fictional; every domain uses the
reserved `.example` top-level domain, so no generated link can reach anyone."""

from __future__ import annotations

SHOP_NAMES = [
    ("Alpine Outfitters", "outdoor"),
    ("Brightloom Home", "home"),
    ("Cobalt Audio", "audio"),
    ("Driftwood Kitchen Supply", "kitchen"),
    ("Ember & Oak Furniture", "furniture"),
    ("Fernway Garden Co.", "garden"),
    ("Granite Cycles", "bikes"),
    ("Harbor Lane Apparel", "apparel"),
    ("Ironvale Tools", "tools"),
    ("Juniper Tech Market", "tech"),
    ("Kestrel Camera House", "camera"),
    ("Lumen Desk Goods", "office"),
]

PRODUCT_NOUNS: dict[str, list[str]] = {
    "outdoor": ["Trail Jacket", "Down Vest", "Trekking Poles", "Dome Tent", "Sleeping Bag", "Hiking Boots", "Rain Shell", "Day Pack", "Camp Stove", "Headlamp", "Merino Base Layer", "Gaiters"],
    "home": ["Linen Duvet Cover", "Wool Throw", "Ceramic Vase", "Table Lamp", "Bath Towel Set", "Cotton Rug", "Scented Candle", "Wall Mirror", "Cushion Cover", "Storage Basket", "Floor Lamp", "Quilt"],
    "audio": ["Studio Headphones", "Bookshelf Speakers", "Turntable", "DAC Amplifier", "Wireless Earbuds", "Soundbar", "Subwoofer", "Phono Preamp", "Portable Speaker", "Microphone", "Cable Kit", "Headphone Stand"],
    "kitchen": ["Chef's Knife", "Cast Iron Skillet", "Dutch Oven", "Cutting Board", "Pour-Over Kettle", "Stand Mixer", "Saucepan Set", "Spice Grinder", "Baking Sheet", "Measuring Cups", "Whisk", "Mortar and Pestle"],
    "furniture": ["Oak Dining Table", "Lounge Chair", "Bookcase", "Bedside Table", "Bar Stool", "Writing Desk", "Sideboard", "Coffee Table", "Wardrobe", "Bench", "Shoe Rack", "Armchair"],
    "garden": ["Pruning Shears", "Raised Bed Kit", "Watering Can", "Garden Hose", "Compost Bin", "Seed Starter Tray", "Trowel", "Garden Kneeler", "Plant Stand", "Bird Feeder", "Rain Barrel", "Hedge Trimmer"],
    "bikes": ["Gravel Bike", "City Bike", "Bike Helmet", "Bike Lock", "Saddle Bag", "Floor Pump", "Bike Light Set", "Cycling Gloves", "Bottle Cage", "Kickstand", "Mudguard Set", "Chain Lube"],
    "apparel": ["Oxford Shirt", "Chino Trousers", "Wool Sweater", "Denim Jacket", "Linen Shirt", "Rain Coat", "Knit Beanie", "Canvas Sneakers", "Cotton Tee", "Puffer Jacket", "Corduroy Pants", "Scarf"],
    "tools": ["Cordless Drill", "Socket Set", "Claw Hammer", "Tape Measure", "Spirit Level", "Jigsaw", "Workbench", "Screwdriver Set", "Utility Knife", "Clamp Set", "Sanding Block", "Tool Bag"],
    "tech": ["Mechanical Keyboard", "USB-C Hub", "Webcam", "Laptop Stand", "Wireless Mouse", "Monitor Arm", "Portable SSD", "Power Bank", "Smart Plug", "Desk Charger", "Noise Machine", "E-Reader"],
    "camera": ["Mirrorless Body", "Prime Lens", "Zoom Lens", "Tripod", "Camera Strap", "Lens Filter", "Memory Card", "Camera Bag", "Flash Unit", "Lens Cleaning Kit", "Remote Shutter", "Battery Grip"],
    "office": ["Notebook Set", "Fountain Pen", "Desk Organizer", "Letter Tray", "Monitor Riser", "Desk Pad", "Paper Shredder", "Label Maker", "Bulletin Board", "Stapler", "Pen Cup", "Planner"],
}

ADJECTIVES = ["Classic", "Summit", "Nordic", "Urban", "Compact", "Pro", "Everyday", "Heritage", "Light", "Studio", "Coastal", "Field", "Prime", "Atlas", "Meridian", "Verde", "Slate", "Aurora"]

BRANDS = ["Northcairn", "Tessel", "Wrenford", "Bluefen", "Halvard", "Ostrava Works", "Pellucid", "Quillon", "Rookwood", "Sablewick"]

COMPANIES = ["Arclight Analytics", "Bramblecode", "Cinderpath Logistics", "Dovetail Health", "Evergrove Energy", "Foxglove Media", "Gullwing Robotics", "Hollowmere Bank", "Inkwell Studios", "Jadestone Labs", "Kiteline Travel", "Larkspur Foods", "Mossbank Insurance", "Nettlefield Retail", "Oxbow Software", "Pinemarten Games"]

JOB_TITLES = ["Senior Backend Engineer", "Data Analyst", "Product Designer", "DevOps Engineer", "Customer Success Manager", "Frontend Developer", "Machine Learning Engineer", "Marketing Coordinator", "QA Engineer", "Technical Writer", "Sales Development Representative", "Site Reliability Engineer", "Mobile Developer", "Finance Associate", "Recruiter", "Support Specialist", "Engineering Manager", "UX Researcher", "Security Analyst", "Office Manager"]

CITIES_US = ["Portland, OR", "Austin, TX", "Denver, CO", "Raleigh, NC", "Madison, WI", "Boise, ID"]
CITIES_EU = ["Berlin", "Lyon", "Rotterdam", "Porto", "Dublin", "Vienna"]
CITIES_UK = ["Leeds", "Bristol", "Glasgow", "Manchester", "Cardiff", "Norwich"]

STREETS = ["Maple Avenue", "Harbor Road", "Linden Street", "Quarry Lane", "Willow Court", "Station Road", "Orchard Way", "Mill Street", "Cedar Drive", "Riverside Walk", "Chapel Row", "Elm Terrace", "Bakers Lane", "Foundry Street", "Hillcrest Road"]

EVENT_WORDS = ["Jazz Night", "Indie Showcase", "Comedy Hour", "Food Truck Rally", "Tech Meetup", "Open Air Cinema", "Poetry Slam", "Craft Market", "Chamber Concert", "Family Science Day", "Gallery Opening", "Startup Pitch Night", "Salsa Workshop", "Board Game Social", "Wine Tasting", "Choir Festival", "Half Marathon", "Photography Walk"]
EVENT_PREFIX = ["Autumn", "Late", "Riverside", "Midweek", "Harvest", "Downtown", "Moonlit", "Grand", "Little", "Northern"]
VENUES = ["The Foundry Hall", "Riverside Pavilion", "Old Mill Theatre", "Civic Gardens", "Lantern Rooms", "Union Warehouse", "Glasshouse Arts Centre", "St. Brigid's Hall", "The Copper Kettle", "Harbour Stage"]

DISHES: dict[str, list[str]] = {
    "Starters": ["Charred Leek Soup", "Burrata with Peaches", "Crispy Calamari", "Beetroot Carpaccio", "Smoked Trout Pate", "Mushroom Croquettes", "Chickpea Fritters", "Tuna Tartare"],
    "Mains": ["Braised Short Rib", "Wild Mushroom Risotto", "Roast Hake", "Lamb Shoulder", "Aubergine Parmigiana", "Chicken Milanese", "Spiced Lentil Dal", "Ribeye Steak", "Pumpkin Gnocchi", "Seafood Linguine"],
    "Desserts": ["Dark Chocolate Tart", "Lemon Posset", "Sticky Toffee Pudding", "Pistachio Gelato", "Apple Crumble", "Basque Cheesecake"],
    "Drinks": ["House Lemonade", "Espresso Tonic", "Ginger Kombucha", "Elderflower Spritz", "Craft Lager", "Pinot Noir (glass)"],
}
RESTAURANTS = ["Saltbox Kitchen", "The Gilded Fern", "Osteria Lucciola", "Copperpot Bistro", "Marlow & Thyme", "Little Tamarind"]

SAAS = ["Ledgerly", "Pingwell", "Formstack Lite", "Shiplane", "Quotient CRM", "Taskhaven", "Beaconly"]
SAAS_FEATURES = ["Unlimited projects", "Email support", "Priority support", "SSO (SAML)", "Audit log", "API access", "Custom domains", "Advanced analytics", "Role-based access", "99.9% uptime SLA", "Data export", "Two-factor authentication", "Webhooks", "Dedicated account manager", "Sandbox environment", "Custom integrations"]

FACILITY_NAMES = ["Northside", "Kingsmead", "Ashgrove", "Fairfield", "Brookhaven", "Millbrook", "Westcliff", "Oakridge", "Riverbend", "Stonebridge", "Elmstead", "Highfield", "Larchmont", "Moorgate", "Pennywell", "Queensway", "Redhill", "Sandown", "Thornbury", "Underhill", "Valemont", "Whitmore", "Yarrow", "Bellmoor", "Copperfield"]
FACILITY_TYPES = {"library": "Library", "pool": "Swimming Pool", "sports_hall": "Sports Hall", "community_centre": "Community Centre", "park": "Park"}
DISTRICTS = ["North", "South", "East", "West", "Central"]
CITIES_GOV = ["Norbridge", "Eastholm", "Wexcombe", "Larkfield"]

PERSON_FIRST = ["Dana", "Marek", "Priya", "Tomas", "Leila", "Owen", "Hana", "Rafael", "Ines", "Callum", "Mei", "Jonas"]
PERSON_LAST = ["Kowalski", "Okafor", "Lindqvist", "Moreau", "Haddad", "Brennan", "Sato", "Varga", "Castillo", "Novak"]
# Personal webmail addresses in the synthetic web use fictional .example providers.
FREE_MAIL = ["mailbox.example", "webpost.example"]
