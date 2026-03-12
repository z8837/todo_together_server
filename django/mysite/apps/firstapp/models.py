from django.db import models

class Log(models.Model):
	level = models.IntegerField(default=0)
	account = models.CharField(default='', max_length=20)
	authentication = models.CharField(default='', max_length=50)
	group = models.CharField(default='', max_length=20)
	ip = models.CharField(default='', max_length=20)
	platform = models.CharField(default='', max_length=10)
	version = models.CharField(default='', max_length=10)
	category = models.CharField(default='', max_length=50)
	content = models.CharField(default='', max_length=2048)
	date = models.DateTimeField(auto_now=True)

	

# Create your models here.
