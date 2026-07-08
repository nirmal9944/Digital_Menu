import qrcode
from io import BytesIO
import base64
import re
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

def qr_generator_view(request):
    """Main page for QR generator"""
    return render(request, 'qr_code_gen/qr_generator.html')

@csrf_exempt
def generate_qr(request):
    """Generate QR code with table number detection"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            url = data.get('url', '')
            qr_color = data.get('qr_color', '#000000')
            bg_color = data.get('bg_color', '#FFFFFF')
            
            # Extract table number from URL
            table_number = extract_table_number(url)
            
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_H,
                box_size=10,
                border=4,
            )
            qr.add_data(url)
            qr.make(fit=True)
            
            # Create QR image with custom colors
            img = qr.make_image(
                fill_color=qr_color,
                back_color=bg_color
            )
            
            # Convert to base64
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            return JsonResponse({
                'success': True,
                'qr_image': img_str,
                'table_number': table_number,
                'url': url
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

def extract_table_number(url):
    """Extract table number from URL"""
    # Pattern to match numbers at the end of URL
    pattern = r'/(\d+)/?$'
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    
    # Alternative: match any number in the URL
    pattern2 = r'/(\d+)'
    match2 = re.search(pattern2, url)
    if match2:
        return match2.group(1)
    
    return 'Unknown'