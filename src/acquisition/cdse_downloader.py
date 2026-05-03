# src/acquisition/cdse_downloader.py
"""
Downloads real Sentinel-2 L2A data for Swat Valley from Copernicus Data Space Ecosystem (CDSE)
Uses corrected OData v4 syntax for the catalogue API
"""

import os
import json
import time
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional
import requests
from tqdm import tqdm
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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
        self.client_id = client_id or os.getenv('CDSE_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('CDSE_CLIENT_SECRET')
        self.token = None
        self.token_expiry = 0
        
        # CDSE API endpoints
        self.token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        self.search_url = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
        
    def authenticate(self) -> bool:
        """
        Get OAuth2 token for API access
        """
        print("🔐 Authenticating with Copernicus Data Space...")
        
        # Check credentials
        if not self.client_id:
            print("❌ CDSE_CLIENT_ID not found in environment variables")
            return False
        if not self.client_secret:
            print("❌ CDSE_CLIENT_SECRET not found in environment variables")
            return False
        
        print(f"✓ Client ID: {self.client_id[:10]}...")
        
        # OAuth2 client credentials flow
        payload = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials'
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(self.token_url, data=payload, headers=headers)
            
            if response.status_code != 200:
                print(f"❌ Authentication failed with status {response.status_code}")
                print(f"   Response: {response.text[:200]}")
                return False
            
            token_data = response.json()
            self.token = token_data['access_token']
            # Token typically expires in 1800 seconds (30 minutes)
            expires_in = token_data.get('expires_in', 1800)
            self.token_expiry = time.time() + expires_in
            
            print(f"✅ Authentication successful. Token expires in {expires_in}s")
            return True
            
        except Exception as e:
            print(f"❌ Authentication failed: {e}")
            return False
    
    def search_swat_tiles(self, start_date: date, end_date: date, max_clouds: int = 30) -> List[Dict]:
        """
        Search for Sentinel-2 products covering Swat Valley
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            max_clouds: Maximum cloud cover percentage
            
        Returns:
            List of product dictionaries
        """
        if not self.token:
            if not self.authenticate():
                return []
        
        # Check token expiration
        if time.time() >= self.token_expiry:
            print("Token expired, re-authenticating...")
            if not self.authenticate():
                return []
        
        # Swat Valley bounding box coordinates (WGS84)
        min_lon, min_lat = 72.5, 34.7
        max_lon, max_lat = 72.8, 35.0
        
        # CORRECTED: Build WKT polygon for CDSE OData API
        # Format: geography'SRID=4326;POLYGON((lon1 lat1, lon2 lat2, lon3 lat3, lon4 lat4, lon1 lat1))'
        polygon_wkt = f"geography'SRID=4326;POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, {max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))'"
        
        # CORRECTED: OData v4 query syntax
        # Using proper OData operators (eq, gt, lt, le, and)
        query = (
            f"CollectionName eq 'SENTINEL-2' and "
            f"ContentDate/Start gt {start_date.isoformat()}T00:00:00.000Z and "
            f"ContentDate/Start lt {end_date.isoformat()}T23:59:59.999Z"
        )
        
        # Add cloud cover filter if supported
        # Different CDSE endpoints use different attribute paths
        query_with_cloud = query + f" and Attributes/any(a: a/Name eq 'cloudCover' and a/Value le {max_clouds})"
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        
        print(f"\n🔍 Searching for Sentinel-2 tiles from {start_date} to {end_date}...")
        
        # Try with cloud filter first
        try:
            response = requests.get(
                self.search_url, 
                headers=headers, 
                params={'$filter': query_with_cloud, '$top': 20},
                timeout=30
            )
            
            if response.status_code == 400:
                # Cloud filter syntax not supported, try without it
                print("   Cloud filter not supported, searching without it...")
                response = requests.get(
                    self.search_url, 
                    headers=headers, 
                    params={'$filter': query, '$top': 20},
                    timeout=30
                )
            
            response.raise_for_status()
            
            data = response.json()
            products = data.get('value', [])
            
            # Apply cloud cover filtering manually if needed
            if products and 'cloudCover' not in str(products[0].get('Attributes', {})):
                print(f"   Found {len(products)} products (cloud filter will be applied after search)")
            
            print(f"📊 Found {len(products)} products")
            
            # Display product info
            for i, prod in enumerate(products[:3]):  # Show first 3
                name = prod.get('Name', 'Unknown')
                cloud = prod.get('CloudCover', 'N/A')
                date_str = prod.get('ContentDate', {}).get('Start', 'Unknown')[:10]
                print(f"   {i+1}. {date_str} - Cloud: {cloud}% - {name[:60]}...")
            
            if len(products) > 3:
                print(f"   ... and {len(products) - 3} more")
            
            # Sort by cloud cover (lowest first) if available
            products.sort(key=lambda x: x.get('CloudCover', 100))
            
            return products
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Search failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Response: {e.response.text[:300]}")
            return []
    
    def get_product_download_url(self, product_id: str) -> Optional[str]:
        """
        Get the download URL for a product
        
        Args:
            product_id: Product ID from search results
            
        Returns:
            Download URL or None
        """
        # OData endpoint for product value (download)
        download_endpoint = f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        
        headers = {
            'Authorization': f'Bearer {self.token}'
        }
        
        try:
            # Make a HEAD request to check if it's accessible
            response = requests.head(download_endpoint, headers=headers, timeout=30)
            response.raise_for_status()
            return download_endpoint
        except Exception as e:
            print(f"   Could not get download URL: {e}")
            return None
    
    def download_product(self, product: Dict, output_dir: str) -> bool:
        """
        Download a Sentinel-2 product
        
        Args:
            product: Product dictionary from search
            output_dir: Directory to save the product
            
        Returns:
            bool: True if download successful
        """
        product_id = product.get('Id')
        product_name = product.get('Name', f'product_{product_id}')
        
        if not product_id:
            print("   No product ID found")
            return False
        
        # Get download URL
        download_url = self.get_product_download_url(product_id)
        if not download_url:
            return False
        
        output_path = Path(output_dir) / f"{product_name}.zip"
        
        # Skip if already downloaded
        if output_path.exists():
            print(f"⏭️ Already downloaded: {product_name}")
            return True
        
        print(f"📥 Downloading: {product_name}")
        print(f"   Size: {product.get('ContentLength', 'Unknown')} bytes")
        
        headers = {'Authorization': f'Bearer {self.token}'}
        
        try:
            response = requests.get(download_url, headers=headers, stream=True, timeout=60)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(output_path, 'wb') as f:
                if total_size > 0:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc=product_name[:40]) as pbar:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                            pbar.update(len(chunk))
                else:
                    # No content-length header, download without progress bar
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # Verify download
            if output_path.stat().st_size > 0:
                print(f"✅ Saved to: {output_path} ({output_path.stat().st_size / 1024 / 1024:.2f} MB)")
                return True
            else:
                print(f"❌ Downloaded file is empty")
                output_path.unlink()  # Remove empty file
                return False
            
        except Exception as e:
            print(f"❌ Download failed: {e}")
            if output_path.exists():
                output_path.unlink()  # Remove partial download
            return False

def download_swat_timestamps():
    """
    Downloads three timestamps for Swat Valley deforestation detection:
    1. Baseline: April 2023 (dry season)
    2. Mid: October 2023 (post-monsoon)
    3. Recent: April 2024
    """
    
    # Create directories
    os.makedirs('data/raw/baseline', exist_ok=True)
    os.makedirs('data/raw/mid', exist_ok=True)
    os.makedirs('data/raw/recent', exist_ok=True)
    os.makedirs('data/cache', exist_ok=True)
    
    print("="*60)
    print("GeoGuard - Sentinel-2 Data Acquisition for Swat Valley")
    print("="*60)
    
    # Initialize downloader
    downloader = CDSEDownloader()
    
    if not downloader.client_id or not downloader.client_secret:
        print("\n❌ Missing credentials. Please check your .env file.")
        print("   The .env file should be in the project root with:")
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
    
    downloaded_count = 0
    
    for name, (start_str, end_str) in timestamps.items():
        print(f"\n{'='*40}")
        print(f"Processing: {name.upper()} ({start_str} to {end_str})")
        print(f"{'='*40}")
        
        start_date = date.fromisoformat(start_str)
        end_date = date.fromisoformat(end_str)
        
        products = downloader.search_swat_tiles(start_date, end_date, max_clouds=30)
        
        if not products:
            print(f"⚠️ No products found for {name}")
            continue
        
        # Try to download products with low cloud cover
        downloaded = False
        for product in products[:3]:  # Try top 3 products
            cloud_cover = product.get('CloudCover', 100)
            if cloud_cover <= 50:  # Accept up to 50% cloud cover
                output_dir = f'data/raw/{name}'
                if downloader.download_product(product, output_dir):
                    # Save metadata
                    metadata = {
                        'product_name': product.get('Name'),
                        'product_id': product.get('Id'),
                        'cloud_cover': cloud_cover,
                        'acquisition_date': product.get('ContentDate', {}).get('Start'),
                        'downloaded_at': datetime.now().isoformat(),
                        'size_bytes': product.get('ContentLength', 0),
                        'bbox': [72.5, 34.7, 72.8, 35.0]
                    }
                    
                    with open(f'{output_dir}/metadata.json', 'w') as f:
                        json.dump(metadata, f, indent=2)
                    
                    downloaded = True
                    downloaded_count += 1
                    break
                else:
                    print(f"   Failed to download product with {cloud_cover}% cloud cover, trying next...")
        
        if not downloaded:
            print(f"⚠️ Could not download any product for {name}")
    
    print("\n" + "="*60)
    print(f"📊 Download summary: {downloaded_count}/3 timestamps downloaded")
    print("="*60)
    
    if downloaded_count == 0:
        print("\n💡 TROUBLESHOOTING SUGGESTIONS:")
        print("   1. Try manual download via browser: https://dataspace.copernicus.eu/browser/")
        print("   2. Check if your OAuth client has 'Read' permissions in CDSE dashboard")
        print("   3. Verify the bounding box coordinates are correct for Swat Valley")

if __name__ == "__main__":
    download_swat_timestamps()