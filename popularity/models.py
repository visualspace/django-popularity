import logging

from datetime import datetime

from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes import generic

from django.conf import settings

# Characteristic age, default one hour
# After this amount (in seconds) the novelty is exactly 0.5
CHARAGE = float(getattr(settings, 'CHARAGE', 3600))

class ViewTrackerQuerySet(models.query.QuerySet):
    def __init__ (self, model = None, *args, **kwargs):
        super(self.__class__, self).__init__ (model, *args, **kwargs)
        
        from math import log
        self._LOGSCALING = log(0.5)

        self._SQL_AGE ='(NOW() - first_view)'
        self._SQL_RELVIEWS = '(views/%(maxviews)d)'
        self._SQL_RELAGE = '(%(age)s/%(maxage)d)'
        self._SQL_NOVELTY = '(%(factor)s * EXP(%(logscaling)s * %(age)s/%(charage)s) + %(offset)s)'
        self._SQL_POPULARITY = '(views/%(age)s)'
        self._SQL_RELPOPULARITY = '(%(popularity)s/%(maxpopularity)s)'
        self._SQL_RANDOM = 'RAND()'
        
        self._SQL_ORDERING = '%(relview)f * %(relview_sql)s + \
                              %(relage)f  * %(relage_sql)s + \
                              %(novelty)f * %(novelty_sql)s + \
                              %(relpopularity)f * %(relpopularity_sql)s + \
                              %(random)f * %(random_sql)s + \
                              %(offset)f'
    
    def _add_extra(self, field, sql):
        assert self.query.can_filter(), \
                "Cannot change a query once a slice has been taken"
        clone = self._clone()
        clone.query.add_extra({field:sql}, None, None, None, None, None)
        return clone
        
    def select_age(self):
        """ Adds age with regards to NOW to the QuerySet
            fields. """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'  
                        
        return self._add_extra('age', self._SQL_AGE)
        
    def select_relviews(self, relative_to=None):
        """ Adds 'relview', a normalized viewcount, to the QuerySet.
            The normalization occcurs relative to the maximum number of views
            in the current QuerySet, unless specified in 'relative_to'.
            
            The relative number of views should always in the range [0, 1]. """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
        
        if not relative_to:
            relative_to = self
        
        assert relative_to.__class__ == self.__class__, \
                'relative_to should be of type %s but is of type %s' % (self.__class__, relative_to.__class__)
            
        maxviews = relative_to.extra(select={'maxviews':'MAX(views)'}).values('maxviews')[0]['maxviews']
        
        SQL_RELVIEWS = self._SQL_RELVIEWS % {'maxviews' : maxviews}
        
        return self._add_extra('relviews', SQL_RELVIEWS)

    def select_relage(self, relative_to=None):
        """ Adds 'relage', a normalized age, relative to the QuerySet.
            The normalization occcurs relative to the maximum age
            in the current QuerySet, unless specified in 'relative_to'.

            The relative age should always in the range [0, 1]. """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'

        if not relative_to:
            relative_to = self

        assert relative_to.__class__ == self.__class__, \
                'relative_to should be of type %s but is of type %s' % (self.__class__, relative_to.__class__)

        maxage = relative_to.extra(select={'maxage':'MAX(%s)' % self._SQL_AGE}).values('maxage')[0]['maxage']

        SQL_RELAGE = self._SQL_RELAGE % {'age'    : self._SQL_AGE,
                                         'maxage' : maxage}

        return self._add_extra('relage', SQL_RELAGE)


    def select_novelty(self, minimum=0.0):
        """ Compute novelty - this is the age muliplied by a characteristic time.
            After a this characteristic age, the novelty will be half its original
            value (if the minimum is 0). The minimum is needed when this value 
            is used in multiplication.
            
            The novelty value is always in the range [0, 1]. """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
        
        offset = minimum
        factor = 1/(1-offset)
        
        SQL_NOVELTY =  self._SQL_NOVELTY % {'logscaling' : self._LOGSCALING, 
                                            'age'        : self._SQL_AGE,
                                            'charage'    : CHARAGE,
                                            'offset'     : offset, 
                                            'factor'     : factor }

        return self.select_age()._add_extra('novelty', SQL_NOVELTY)
    
    def select_popularity(self):
        """ Compute popularity, which is defined as: views/age. """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
                
        SQL_POPULARITY = self._SQL_POPULARITY % {'age' : self._SQL_AGE }

        return self.select_age()._add_extra('popularity', SQL_POPULARITY)
    
    def select_relpopularity(self, relative_to=None):
        """ Compute relative popularity, which is defined as: (views/age)/MAX(views/age).
            
            The relpopularity value should always be in the range [0, 1]. """

        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
        
        if not relative_to:
            relative_to = self

        assert relative_to.__class__ == self.__class__, \
                'relative_to should be of type %s but is of type %s' % (self.__class__, relative_to.__class__)

        SQL_POPULARITY = self._SQL_POPULARITY % {'age' : self._SQL_AGE }

        maxpopularity = relative_to.extra(select={'maxpopularity':'MAX(%s)' % SQL_POPULARITY}).values('maxpopularity')[0]['maxpopularity']
        
        SQL_RELPOPULARITY = self._SQL_RELPOPULARITY % {'popularity'    : SQL_POPULARITY,
                                                       'maxpopularity' : maxpopularity }

        return self.select_popularity()._add_extra('relpopularity', SQL_POPULARITY)
    
    def select_random(self):
        """ Returns the original QuerySet with an extra field 'random' containing a random
            value in the range [0,1] to use for ordering.
        """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
        
        SQL_RANDOM = self.RANDOM
        
        return self._add_extra('random', SQL_RANDOM)
    
    def select_ordering(relview=0.0, relage=0.0, novelty=0.0, relpopularity=0.0, random=0.0, offset=0.0, relative_to=None):
        """ Creates an 'ordering' field used for sorting the current QuerySet according to
            specified criteria, given by the parameters. 
            
            All the parameters given here are relative to one another, so if you specify 
            random=1.0 and relage=3.0 then the relative age is 3 times as important. 
            
            Please do note that the relative age is the only value here that INCREASES over time so
            you might want to specify a NEGATIVE value here and use an offset, just to compensate. 
        """
        assert settings.DATABASE_ENGINE == 'mysql', 'This only works for MySQL.'
        
        if not relative_to:
            relative_to = self
        
        assert relative_to.__class__ == self.__class__, \
                'relative_to should be of type %s but is of type %s' % (self.__class__, relative_to.__class__)
            
        maxviews = relative_to.extra(select={'maxviews':'MAX(views)'}).values('maxviews')[0]['maxviews']
        
        SQL_RELVIEWS = self._SQL_RELVIEWS % {'maxviews' : maxviews}
        
        maxage = relative_to.extra(select={'maxage':'MAX(%s)' % self._SQL_AGE}).values('maxage')[0]['maxage']

        SQL_RELAGE = self._SQL_RELAGE % {'age'    : self._SQL_AGE,
                                         'maxage' : maxage}
        
        # Here, because the ordering field is not normalize, we don't have to bother about a minimum for the novelty
        SQL_NOVELTY =  self._SQL_NOVELTY % {'logscaling' : self._LOGSCALING, 
                                            'age'        : self._SQL_AGE,
                                            'charage'    : CHARAGE,
                                            'offset'     : 0.0, 
                                            'factor'     : 1.0 }
                                            
        SQL_POPULARITY = self._SQL_POPULARITY % {'age' : self._SQL_AGE }

        maxpopularity = relative_to.extra(select={'maxpopularity':'MAX(%s)' % SQL_POPULARITY}).values('maxpopularity')[0]['maxpopularity']

        SQL_RELPOPULARITY = self._SQL_RELPOPULARITY % {'popularity'    : SQL_POPULARITY,
                                                       'maxpopularity' : maxpopularity }
        
        SQL_RANDOM = self.RANDOM
        
        self._SQL_ORDERING = '%(relview)f * %(relview_sql)s + \
                              %(relage)f  * %(relage_sql)s + \
                              %(novelty)f * %(novelty_sql)s + \
                              %(relpopularity)f * %(relpopularity_sql)s + \
                              %(random)f * %(random_sql)s + \
                              %(offset)f'
                              
        SQL_ORDERING = self._SQL_ORDERING % {'relview'           : relview,
                                             'relage'            : relage,
                                             'novelty'           : novelty,
                                             'relpopularity'     : relpopularity,
                                             'random'            : random,
                                             'relview_sql'       : SQL_RELVIEWS,
                                             'relage_sql'        : SQL_RELAGE,
                                             'novelty_sql'       : SQL_NOVELTY,
                                             'relpopularity_sql' : SQL_RELPOPULARITY,
                                             'random_sql'        : SQL_RANDOM }
        
        return self._add_extra('ordering', SQL_ORDERING)
        
    def get_recently_viewed(self, limit=10):
        """ Returns the most recently viewed objects. """
        return self.order_by('-last_view').limit(limit)
    
    def get_recently_added(self, limit=10):
        """ Returns the objects with the most rcecent first_view. """
        return self.order_by('-first_view').limit(limit)
    
    def get_for_model(self, model):
        """ Returns the objects and its views for a certain model. """
        return self.get_for_models([model])
    
    def get_for_models(self, models):
        """ Returns the objects and its views for specified models. """

        cts = []
        for model in models:
            cts.append(ContentType.objects.get_for_model(model))
        
        return self.filter(content_type__in=cts)
    
    def get_for_object(self, content_object, create=False):
        """ Gets the viewtracker for specified object, or creates one 
            if requested. """
        
        ct = ContentType.objects.get_for_model(content_object)
        
        if create:
            [viewtracker, created] = self.get_or_create(content_type=ct, object_id=content_object.pk)
        else:
            viewtracker = self.get(content_type=ct, object_id=content_object.pk)
        
        return viewtracker
    
    def get_for_objects(self, objects):
        """ Gets the viewtrackers for specified objects, or creates them 
            if requested. """

        qs = self.none()
        for obj in objects:
            ct = ContentType.objects.get_for_model(obj.__class__)
            
            qs = qs | self.filter(content_type=ct, object_id=obj.pk)
        
        return self & qs

class ViewTrackerManager(models.Manager):
    """ Manager methods to do stuff like:
        ViewTracker.objects.get_views_for_model(MyModel).
        
        For documentation, please refer the ViewTrackerQuerySet object.
    """
    
    def get_query_set(self):
		return ViewTrackerQuerySet(self.model)
        
    def select_age(self, *args, **kwargs):
        return self.get_query_set().select_age(*args, **kwargs)

    def select_relage(self, *args, **kwargs):
        return self.get_query_set().select_relage(*args, **kwargs)
                    
    def select_relviews(self, *args, **kwargs):
        return self.get_query_set().select_relviews(*args, **kwargs)

    def select_novelty(self, *args, **kwargs):
        return self.get_query_set().select_novelty(*args, **kwargs)
    
    def select_popularity(self, *args, **kwargs):
        return self.get_query_set().select_popularity(*args, **kwargs)

    def select_relpopularity(self, *args, **kwargs):
        return self.get_query_set().select_relpopularity(*args, **kwargs)

    def get_recently_added(self, *args, **kwargs):
        return self.get_query_set().get_recently_added(*args, **kwargs)
    
    def get_recently_viewed(self, *args, **kwargs):
        return self.get_query_set().get_recently_viewed(*args, **kwargs)
    
    def get_for_model(self, *args, **kwargs):
        return self.get_query_set().get_for_model(*args, **kwargs)
    
    def get_for_models(self, *args, **kwargs):
        return self.get_query_set().get_for_models(*args, **kwargs)
    
    def get_for_object(self, *args, **kwargs):
        return self.get_query_set().get_for_object(*args, **kwargs)
    
    def get_for_objects(self, *args, **kwargs):
        return self.get_query_set().get_for_objects(*args, **kwargs)


class ViewTracker(models.Model):
    """ The ViewTracker object does exactly what it's supposed to do:
        track the amount of views for an object in order to create make 
        a popularity rating."""
    
    content_type = models.ForeignKey(ContentType)
    object_id = models.PositiveIntegerField()
    content_object = generic.GenericForeignKey('content_type', 'object_id')
    
    first_view = models.DateTimeField(auto_now_add=True)
    last_view = models.DateTimeField(auto_now=True)
    
    views = models.PositiveIntegerField(default=0)
    
    objects = ViewTrackerManager()
    
    class Meta:
        get_latest_by = 'last_view'
        ordering = ['-views', '-last_view', 'first_view']
        unique_together = ('content_type', 'object_id')
    
    def __unicode__(self):
        return u"%s, %d views" % (self.content_object, self.views)
    
    def increment(self):
        """ This increments my view count.
            TODO: optimize in SQL. """
        #logging.debug('Incrementing views for %s from %d to %d' % (self.content_object, self.views, self.views+1))
        self.views = self.views + 1
        self.save()
    
    def get_age(self, refdate=None):
        """ Gets the age of an object relating to a reference date 
            (defaults to now). """
        if not refdate:
            refdate = datetime.now()
        
        assert refdate >= self.first_view, 'Reference date should be equal to or higher than the first view.'
        
        return refdate - self.first_view
        
    @classmethod
    def add_view_for(cls, content_object):
        """ This increments the viewcount for a given object. """
        viewtracker = cls.objects.get_for_object(content_object, create=True)
        
        viewtracker.increment()
        
        return viewtracker
    
    @classmethod
    def get_views_for(cls, content_object):
        """ Gets the total number of views for content_object. """
        ct = ContentType.objects.get_for_model(content_object)
        
        """ If we don't have any views, return 0. """
        try:
            viewtracker = cls.objects.get_for_object(content_object)
        except ViewTracker.DoesNotExist:
            return 0 
        
        return viewtracker.views