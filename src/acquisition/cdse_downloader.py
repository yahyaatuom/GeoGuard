# src/acquisition/cdse_downloader.py (UPDATED)
"""
Downloads real Sentinel-2 L2A data for Swat Valley from Copernicus Data Space Ecosystem (CDSE)
"""

import os
import json
import time
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional

# 🔧 ADD THIS - Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()  # Looks for .env file in the current directory

import requests
from tqdm import tqdm

class CDSEDownloader:
    """
    Handles authentication and download from Copernicus Data Space Ecosystem
    """
    
    def __init__(self, client_id: str = None, client_secret: str = None):
        """
        Args:
            client_id: OAuth2 client ID (from CDSE dashboard)
            client_secret: OAuth2 client secret
        """
        # 🔧 ADD DEBUGGING - Print if credentials are found
        self.client_id = client_id or os.getenv('CDSE_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('CDSE_CLIENT_SECRET')
        
        # Check if credentials were loaded
        if self.client_id:
            print(f"✓ Found CDSE_CLIENT_ID (first 10 chars: {self.client_id[:10]}...)")
        else:
            print("✗ CDSE_CLIENT_ID not found in environment variables")
            
        if self.client_secret:
            print(f"✓ Found CDSE_CLIENT_SECRET (first 5 chars: {self.client_secret[:5]}...)")
        else:
            print("✗ CDSE_CLIENT_SECRET not found in environment variables")
        
        self.token = None
        self.token_expiry = 0
        
        # CDSE API endpoints
        self.token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        self.search_url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
        
    def authenticate(self) -> bool:
        """
        Get OAuth2 token for API access using client credentials flow
        """
        print("\n🔐 Authenticating with Copernicus Data Space...")
        
        # 🔧 FIX - Use correct OAuth2 payload format for token endpoint
        # The payload should be form-encoded, not JSON
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials'
        }
        
        # 🔧 IMPORTANT - Use 'data' parameter for form encoding (not 'json')
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(self.token_url, data=payload, headers=headers)
            
            # Debug info
            print(f"   Status code: {response.status_code}")
            
            response.raise_for_status()
            
            token_data = response.json()
            self.token = token_data['access_token']
            self.token_expiry = time.time() + token_data.get('expires_in', 3600)
            
            print(f"✅ Authentication successful. Token expires in {token_data.get('expires_in', 3600)}s")
            return True
            
        except requests.exceptions.HTTPError as e:
            print(f"❌ HTTP Error: {e}")
            if response.status_code == 401:
                print("   → This usually means: Client ID or Secret is incorrect")
                print("   → Or the OAuth client wasn't created properly in CDSE dashboard")
            return False
        except Exception as e:
            print(f"❌ Authentication failed: {e}")
            return False
    
    # The rest of the class remains the same...
    def search_swat_tiles(self, start_date: date, end_date: date, max_clouds: int = 30) -> List[Dict]:
        if not self.token:
            if not self.authenticate():
                return []
        
        swat_bbox = "72.5,34.7,72.8,35.0"
        
        query = (
            f"CollectionName='SENTINEL-2' AND "
            f"ContentDate/Start gt {start_date.isoformat()}T00:00:00Z AND "
            f"ContentDate/Start lt {end_date.isoformat()}T23:59:59Z AND "
            f"Attributes/OData.CSC.StringAttribute/any(att: att/Name eq 'cloudCover' and att/OData.CSC.DoubleAttribute/Value le {max_clouds}) AND "
            f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(({swat_bbox}))')"
        )
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        
        print(f"\n🔍 Searching for Sentinel-2 tiles from {start_date} to {end_date}...")
        
        try:
            response = requests.get(self.search_url, headers=headers, params={'$filter': query})
            response.raise_for_status()
            
            products = response.json().get('value', [])
            print(f"📊 Found {len(products)} products")
            
            products.sort(key=lambda x: x.get('CloudCover', 100))
            
            return products
            
        except Exception as e:
            print(f"❌ Search failed: {e}")
            return []
    
    def download_product(self, product: Dict, output_dir: str) -> bool:
        product_id = product.get('Id')
        product_name = product.get('Name')
        
        if not product_id:
            return False
        
        download_url = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        
        headers = {'Authorization': f'Bearer {self.token}'}
        
        output_path = Path(output_dir) / f"{product_name}.zip"
        
        print(f"📥 Downloading: {product_name}")
        
        try:
            response = requests.get(download_url, headers=headers, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(output_path, 'wb') as f:
                with tqdm(total=total_size, unit='B', unit_scale=True, desc=product_name) as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        pbar.update(len(chunk))
            
            print(f"✅ Saved to: {output_path}")
            return True
            
        except Exception as e:
            print(f"❌ Download failed: {e}")
            return False

def download_swat_timestamps():
    """Downloads three timestamps for Swat Valley"""
    
    os.makedirs('data/raw/baseline', exist_ok=True)
    os.makedirs('data/raw/mid', exist_ok=True)
    os.makedirs('data/raw/recent', exist_ok=True)
    os.makedirs('data/cache', exist_ok=True)
    
    downloader = CDSEDownloader()
    
    if not downloader.client_id or not downloader.client_secret:
        print("\n❌ Missing credentials. Please check your .env file.")
        print("   The .env file should be in: C:\\Users\\Dell\\Desktop\\GeoGuard\\.env")
        print("   And contain:")
        print("   CDSE_CLIENT_ID=your_actual_client_id")
        print("   CDSE_CLIENT_SECRET=your_actual_client_secret")
        return
    
    if not downloader.authenticate():
        print("Cannot proceed without authentication")
        return
    
    timestamps = {
        'baseline': ('2023-04-01', '2023-04-30'),
        'mid': ('2023-10-01', '2023-10-31'),
        'recent': ('2024-04-01', '2024-04-30')
    }
    
    for name, (start_str, end_str) in timestamps.items():
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
        
        products = downloader.search_swat_tiles(start_date, end_date, max_clouds=30)
        
        if products:
            best = products[0]
            output_dir = f'data/raw/{name}'
            downloader.download_product(best, output_dir)
            
            metadata = {
                'product_name': best.get('Name'),
                'cloud_cover': best.get('CloudCover'),
                'acquisition_date': best.get('ContentDate', {}).get('Start'),
                'downloaded_at': datetime.now().isoformat()
            }
            
            with open(f'{output_dir}/metadata.json', 'w') as f:
                json.dump(metadata, f, indent=2)
        else:
            print(f"⚠️ No suitable products found for {name}")
    
    print("\n" + "="*50)
    print("✅ Download complete. Check data/raw/ directory")
    print("="*50)

if __name__ == "__main__":
    download_swat_timestamps()