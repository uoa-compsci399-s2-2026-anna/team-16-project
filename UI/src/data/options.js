export const SUPPLY_CHAIN_STAGES = [
  {
    name: 'Primary production',
    description: 'Growing, farming, fishing or harvesting food.',
    details: 'This includes farms, orchards, vineyards, fisheries and other activities where food is grown, raised or harvested. Examples include crops left unharvested, rejected produce and food lost during initial handling.',
  },
  {
    name: 'Manufacturing and processing',
    description: 'Producing, preparing, processing or packaging food.',
    details: 'This includes factories, bakeries, packhouses and processing facilities. Examples include trimming losses, damaged products, production errors, rejected batches and food lost during packaging.',
  },
  {
    name: 'Distribution and wholesale',
    description: 'Storing or moving food between producers and sellers.',
    details: 'This includes distributors, wholesalers, warehouses and logistics operations. Examples include storage losses, transport damage and products that cannot be distributed.',
  },
  {
    name: 'Retail',
    description: 'Selling food directly to customers.',
    details: 'This includes supermarkets, grocery stores, markets and other food retailers. Examples include expired stock, damaged products, unsold food and produce removed from display.',
  },
  {
    name: 'Hospitality and food service',
    description: 'Preparing or serving food outside the home.',
    details: 'This includes restaurants, cafés, hotels, catering businesses and commercial kitchens. Examples include preparation waste, overproduction, buffet waste and customer plate waste.',
  },
  {
    name: 'Institutions',
    description: 'Preparing or serving food in public or community organisations.',
    details: 'This includes schools, universities, hospitals, aged-care facilities, prisons and workplace cafeterias. Examples include kitchen preparation waste, overproduction and uneaten meals.',
  },
]

export const WASTE_DESTINATIONS = [
  'Landfill',
  'Sewer',
  'Compost / Aerobic digestion',
  'Anaerobic digestion',
  'Land application',
  'Controlled combustion',
  'Bio-based material / biochemical processing',
  'Refuse / discards',
  'Not harvested / ploughed-in',
]

export const FOOD_CATEGORIES = [
  'General food waste — type unknown',
  'Fruit',
  'Vegetables',
  'Bakery products',
  'Dairy and eggs',
  'Meat',
  'Seafood',
  'Beverages',
  'Mixed food',
  'Other',
]

export const UNITS = ['kilograms', 'tonnes']
