"""
Django settings for inventoryproject project.

Generated by 'django-admin startproject' using Django 3.1.7.

For more information on this file, see
https://docs.djangoproject.com/en/3.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/3.1/ref/settings/
"""
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from pathlib import Path


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '@ujr-e&a%8m%6!z(+ka16+(sm6cug(h6noe%#p%=6%d2nz5t+#'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = ['a1connect.onrender.com', '.now.sh', '127.0.0.1', 'localhost']
# Application definition

INSTALLED_APPS = [
    'jazzmin',
    'cloudinary_storage',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'dashboard.apps.DashboardConfig',
    'user.apps.UserConfig',
    'crispy_forms',
    'cloudinary',
]



# Cloudinary Configuration
cloudinary.config(
    cloud_name='dkhobecps',
    api_key='657146181452448',
    api_secret='5fb1dAK6SU2veve8exLK7TVLXFA',
)


# Cloudinary Storage Configuration
CLOUDINARY_STORAGE  = {
    'CLOUD_NAME': 'dkhobecps',
    'API_KEY': '657146181452448',
    'API_SECRET': '5fb1dAK6SU2veve8exLK7TVLXFA',
}
# Set Cloudinary as the default stordkhobecpsage backend
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'

STATIC_URL = 'https://res.cloudinary.com/dkhobecps/'

# Cloudinary configuration (optional if you're using DEFAULT_FILE_STORAGE)

#CLOUDINARY_URL = os.getenv('CLOUDINARY_URL')  # You can set this as an environment variable
#CLOUDINARY_STORAGE = {
#    'CLOUD_NAME': os.getenv('CLOUDINARY_CLOUD_NAME'),
#    'API_KEY': os.getenv('CLOUDINARY_API_KEY'),
#    'API_SECRET': os.getenv('CLOUDINARY_API_SECRET'),
#}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'inventoryproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'inventoryproject.wsgi.application'
# Database
# https://docs.djangoproject.com/en/3.1/ref/settings/#databases

#DATABASES = {
#    'default': {
#        'ENGINE': 'django.db.backends.sqlite3',
#        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
#    }
#}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'railway',  # Remove the parentheses around the database name
        'USER': 'postgres',  # Remove the parentheses around the username
        'PASSWORD': 'hfvSz5SeAtep7bakD7pJ',  # Remove the parentheses around the password
        'HOST': 'containers-us-west-174.railway.app',  # Remove the parentheses around the host
        'PORT': '6045',  # Remove the parentheses around the port
    }
}



# Password validation
# https://docs.djangoproject.com/en/3.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/3.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


CRISPY_TEMPLATE_PACK = 'bootstrap4'
# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.1/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static')
]
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

LOGIN_REDIRECT_URL = 'dashboard-index'

LOGIN_URL = 'user-login'

# settings.py

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

JAZZMIN_SETTINGS = {
    'site_header': "A1tech",
    'site_brand': "Admin Panel",
    'site_title': "A1tech Admin",
    'site_logo': 'https://res.cloudinary.com/dkhobecps/image/upload/v1696708795/media/profile_images/logo_2_fmvthl.png',  # Path to your logo in STATIC_ROOT
    'navigation_expanded': True,
    'site_copyright': "© 2023 A1technical Engineering Solutions",
    'show_powered_by': False,
    'theme': 'light',
    'show_ui_builder': True,
    'JAZZMIN_UI_TWEAKS' :'dark_mode_theme',
    'theme_color': '#FFA500',  # Custom theme color
}

JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": "darkly",
}
